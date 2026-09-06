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
    commit` does NOT preserve (port bindings, restart policy, named-volume
    mounts), so /migrate-in can recreate an equivalent container. `commit`
    bakes in filesystem/image state but never volume contents — that's
    what export_volume_data/import_volume_data below are for; this only
    captures WHERE a volume was mounted, not what's in it.
    """
    container = get_client().containers.get(container_name)
    host_config = container.attrs.get("HostConfig", {})

    ports = {}
    for container_port, bindings in (host_config.get("PortBindings") or {}).items():
        if bindings:
            ports[container_port] = bindings[0].get("HostPort")

    restart_policy = host_config.get("RestartPolicy") or {"Name": "no"}

    volumes = {}
    for mount in container.attrs.get("Mounts", []):
        if mount.get("Type") == "volume":
            volumes[mount["Name"]] = {"bind": mount["Destination"], "mode": "rw" if mount.get("RW", True) else "ro"}

    return {"ports": ports, "restart_policy": restart_policy, "volumes": volumes}


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
    """Creates and starts the container. Removes any existing container
    under the same name first, so this is safe to call again after a
    client-side timeout whose server-side operation actually succeeded
    (the incoming image is authoritative during a migration) — without
    this, a retry hits an unhandled 409 Conflict instead of just redoing
    the (idempotent, from the caller's perspective) work.
    """
    client = get_client()
    try:
        client.containers.get(container_name).remove(force=True)
    except NotFound:
        pass

    return client.containers.run(
        image,
        name=container_name,
        detach=True,
        ports=run_config.get("ports") or None,
        restart_policy=run_config.get("restart_policy") or None,
        volumes=run_config.get("volumes") or None,
    )


_HELPER_IMAGE = "busybox"


def _ensure_image(client: docker.DockerClient, image: str) -> None:
    """containers.create() (unlike .run()) does NOT auto-pull a missing
    image — only surfaced on a host that never happened to have `busybox`
    cached already (a real destination VM, not a dev machine with it
    already pulled from earlier testing). Explicit pull-if-missing here."""
    try:
        client.images.get(image)
    except NotFound:
        client.images.pull(image)


def export_volume_data(volume_name: str) -> bytes:
    """Tars up a named volume's contents via the same get_archive/put_archive
    primitives `docker cp` itself uses — no exec, no shelling out to `tar`,
    and no dependency on the volume's own container being alive (the volume
    outlives stop_commit_remove; that's the point of a named volume). Scoped
    to a single volume per node-agent-CLAUDE.md's stateful-migration note.
    """
    client = get_client()
    _ensure_image(client, _HELPER_IMAGE)
    helper = client.containers.create(_HELPER_IMAGE, command="true", volumes={volume_name: {"bind": "/data", "mode": "ro"}})
    try:
        stream, _ = helper.get_archive("/data")
        return b"".join(stream)
    finally:
        helper.remove(force=True)


def import_volume_data(volume_name: str, tar_bytes: bytes) -> None:
    """Restores a volume's contents (as produced by export_volume_data)
    into a volume on this node, creating it first if it doesn't exist yet —
    must run before run_container so the recreated container starts with
    its data already in place, not racing a fresh container against the
    restore.
    """
    client = get_client()
    _ensure_image(client, _HELPER_IMAGE)
    try:
        client.volumes.get(volume_name)
    except NotFound:
        client.volumes.create(volume_name)

    helper = client.containers.create(_HELPER_IMAGE, command="true", volumes={volume_name: {"bind": "/data", "mode": "rw"}})
    try:
        helper.put_archive("/data", tar_bytes)
    finally:
        helper.remove(force=True)


def wait_until_running(container, retries: int = 10, delay: float = 0.5) -> bool:
    """'Confirm healthy' per node-agent-CLAUDE.md, kept to container-level
    state — arbitrary migrated containers won't have an app-level health
    endpoint we can assume exists."""
    for _ in range(retries):
        container.reload()
        if container.status == "running":
            return True
        time.sleep(delay)
    return False
