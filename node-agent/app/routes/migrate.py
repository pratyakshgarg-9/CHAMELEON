import asyncio
import json
import logging

from docker.errors import NotFound
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from app import docker_client
from app.clients import post_multipart
from app.config import settings
from app.deps import get_registry
from app.models import MigrateInResponse, MigrateOutRequest, MigrateOutResponse
from app.neighbors import PeerRegistry

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/migrate-out", response_model=MigrateOutResponse)
async def migrate_out(body: MigrateOutRequest, registry: PeerRegistry = Depends(get_registry)):
    destination = next((p for p in registry.list_all() if p.node_id == body.destination_node), None)
    if destination is None:
        raise HTTPException(
            400, f"unknown destination_node {body.destination_node!r} (not a known neighbor)"
        )

    try:
        run_config = await asyncio.to_thread(docker_client.get_run_config, body.container_name)
    except NotFound:
        raise HTTPException(404, f"container {body.container_name!r} not found on this node")

    image_tag = await asyncio.to_thread(docker_client.stop_commit_remove, body.container_name)
    tar_bytes = await asyncio.to_thread(docker_client.save_image, image_tag)

    files = {"file": (f"{body.container_name}.tar", tar_bytes, "application/x-tar")}
    data = {"metadata": json.dumps({"container_name": body.container_name, "run_config": run_config})}
    result = await post_multipart(f"{destination.url}/migrate-in", files, data)

    if result is None:
        logger.warning(
            "migrate-out of %s to %s failed — restarting locally", body.container_name, destination.node_id
        )
        container = await asyncio.to_thread(
            docker_client.run_container, image_tag, body.container_name, run_config
        )
        healthy = await asyncio.to_thread(docker_client.wait_until_running, container)
        return MigrateOutResponse(
            status="failed_rolled_back" if healthy else "failed",
            container_name=body.container_name,
            destination_node=None,
            detail="destination unreachable or rejected the migration",
        )

    await asyncio.to_thread(docker_client.remove_image, image_tag)
    return MigrateOutResponse(
        status="migrated",
        container_name=body.container_name,
        destination_node=destination.node_id,
        detail=None,
    )


@router.post("/migrate-in", response_model=MigrateInResponse)
async def migrate_in(file: UploadFile = File(...), metadata: str = Form(...)):
    meta = json.loads(metadata)
    container_name = meta["container_name"]
    run_config = meta.get("run_config") or {}

    tar_bytes = await file.read()
    image = await asyncio.to_thread(docker_client.load_image, tar_bytes)
    container = await asyncio.to_thread(docker_client.run_container, image.id, container_name, run_config)
    healthy = await asyncio.to_thread(docker_client.wait_until_running, container)

    if not healthy:
        raise HTTPException(500, f"container {container_name!r} did not reach a running state")

    return MigrateInResponse(status="running", container_name=container_name, node_id=settings.NODE_ID)
