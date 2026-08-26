import time
import uuid
from typing import Optional

import docker
from docker.errors import NotFound

_client: Optional[docker.DockerClient] = None


def get_client() -> docker.DockerClient:
    global _client
    if _client is None:
        _client = docker.from_env()
    return _client


def get_run_config(container_name: str) -> dict:
    """Captures the parts of a running container's config that `docker
    commit` does NOT preserve (port bindings, restart policy), so
    /migrate-in can recreate an equivalent container. Volumes/mounts are
    intentionally not captured — migrating stateful volume data is out of
    scope for this build.
    """
    container = get_client().containers.get(container_name)
    host_config = container.attrs.get("HostConfig", {})

    ports = {}
    for container_port, bindings in (host_config.get("PortBindings") or {}).items():
        if bindings:
            ports[container_port] = bindings[0].get("HostPort")

    restart_policy = host_config.get("RestartPolicy") or {"Name": "no"}
    return {"ports": ports, "restart_policy": restart_policy}


def stop_commit_remove(container_name: str) -> str:
    """Stops the container, commits it to a freshly tagged image, and
    removes the container — its state now lives entirely in the image, so
    keeping the stopped container around is just dead weight (and would
    collide on the name if something else tries to run under it). Returns
    the new image tag.
    """
    container = get_client().containers.get(container_name)
    container.stop(timeout=10)
    repo = f"chameleon-migrate/{container_name.lower()}"
    tag = uuid.uuid4().hex[:8]
    container.commit(repository=repo, tag=tag)
    container.remove()
    return f"{repo}:{tag}"


def save_image(image_tag: str) -> bytes:
    image = get_client().images.get(image_tag)
    return b"".join(image.save())


def remove_image(image_tag: str) -> None:
    try:
        get_client().images.remove(image_tag, force=True)
    except NotFound:
        pass


def load_image(tar_bytes: bytes):
    images = get_client().images.load(tar_bytes)
    return images[0]


def run_container(image, container_name: str, run_config: dict):
    return get_client().containers.run(
        image,
        name=container_name,
        detach=True,
        ports=run_config.get("ports") or None,
        restart_policy=run_config.get("restart_policy") or None,
    )


def wait_until_running(container, retries: int = 5, delay: float = 0.5) -> bool:
    """'Confirm healthy' per node-agent-CLAUDE.md, kept to container-level
    state — arbitrary migrated containers won't have an app-level health
    endpoint we can assume exists."""
    for _ in range(retries):
        container.reload()
        if container.status == "running":
            return True
        time.sleep(delay)
    return False
