import asyncio
import json
import logging

from docker.errors import NotFound

from app import docker_client
from app.clients import post_multipart
from app.models import MigrateOutResponse
from app.neighbors import PeerRegistry

logger = logging.getLogger(__name__)


class ContainerNotFound(Exception):
    pass


async def migrate_container(
    registry: PeerRegistry, container_name: str, destination_node: str
) -> MigrateOutResponse:
    """The full /migrate-out sequence (stop, commit, remove, transfer, and
    a local rollback if the transfer fails), usable both from the HTTP route
    and directly from the scheduler — callers translate ValueError/
    ContainerNotFound to whatever error shape fits their context.
    """
    destination = next((p for p in registry.list_all() if p.node_id == destination_node), None)
    if destination is None:
        raise ValueError(f"unknown destination_node {destination_node!r} (not a known neighbor)")

    try:
        run_config = await asyncio.to_thread(docker_client.get_run_config, container_name)
    except NotFound:
        raise ContainerNotFound(container_name)

    image_tag = await asyncio.to_thread(docker_client.stop_commit_remove, container_name)
    tar_bytes = await asyncio.to_thread(docker_client.save_image, image_tag)

    files = {"file": (f"{container_name}.tar", tar_bytes, "application/x-tar")}
    data = {"metadata": json.dumps({"container_name": container_name, "run_config": run_config})}
    result = await post_multipart(f"{destination.url}/migrate-in", files, data)

    if result is None:
        logger.warning(
            "migrate-out of %s to %s failed — restarting locally", container_name, destination.node_id
        )
        container = await asyncio.to_thread(
            docker_client.run_container, image_tag, container_name, run_config
        )
        healthy = await asyncio.to_thread(docker_client.wait_until_running, container)
        return MigrateOutResponse(
            status="failed_rolled_back" if healthy else "failed",
            container_name=container_name,
            destination_node=None,
            detail="destination unreachable or rejected the migration",
        )

    await asyncio.to_thread(docker_client.remove_image, image_tag)
    return MigrateOutResponse(
        status="migrated",
        container_name=container_name,
        destination_node=destination.node_id,
        detail=None,
    )
