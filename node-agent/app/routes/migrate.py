import asyncio
import json
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from app import docker_client
from app.config import settings
from app.deps import get_registry
from app.migration import ContainerNotFound, migrate_container
from app.models import MigrateInResponse, MigrateOutRequest, MigrateOutResponse
from app.neighbors import PeerRegistry

router = APIRouter()


@router.post("/migrate-out", response_model=MigrateOutResponse)
async def migrate_out(body: MigrateOutRequest, registry: PeerRegistry = Depends(get_registry)):
    try:
        return await migrate_container(registry, body.container_name, body.destination_node)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except ContainerNotFound:
        raise HTTPException(404, f"container {body.container_name!r} not found on this node")


@router.post("/migrate-in", response_model=MigrateInResponse)
async def migrate_in(
    file: UploadFile = File(...), metadata: str = Form(...), data: Optional[UploadFile] = File(None)
):
    meta = json.loads(metadata)
    container_name = meta["container_name"]
    run_config = meta.get("run_config") or {}

    tar_bytes = await file.read()
    image = await asyncio.to_thread(docker_client.load_image, tar_bytes)

    # Restore volume data BEFORE starting the container, so it comes up
    # with its prior state already in place rather than racing a fresh
    # container against the restore. Same single-volume scope as the
    # /migrate-out side that produced this.
    volumes = run_config.get("volumes") or {}
    if data is not None and volumes:
        volume_name = next(iter(volumes))
        volume_tar_bytes = await data.read()
        await asyncio.to_thread(docker_client.import_volume_data, volume_name, volume_tar_bytes)

    container = await asyncio.to_thread(docker_client.run_container, image.id, container_name, run_config)
    healthy = await asyncio.to_thread(docker_client.wait_until_running, container)

    if not healthy:
        raise HTTPException(500, f"container {container_name!r} did not reach a running state")

    return MigrateInResponse(status="running", container_name=container_name, node_id=settings.NODE_ID)
