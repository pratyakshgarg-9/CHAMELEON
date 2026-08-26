import uuid

import pytest

from app import docker_client

pytestmark = pytest.mark.docker


@pytest.fixture
def busybox_container():
    client = docker_client.get_client()
    name = f"chameleon-test-{uuid.uuid4().hex[:8]}"
    container = client.containers.run(
        "busybox", "sleep 300", name=name, detach=True, ports={}, restart_policy=None
    )
    yield container

    # best-effort cleanup: the container may already have been renamed/
    # removed/replaced by the migration flow under test
    try:
        c = client.containers.get(name)
        c.remove(force=True)
    except Exception:
        pass


def test_migrate_mechanics_end_to_end(busybox_container):
    name = busybox_container.name

    run_config = docker_client.get_run_config(name)
    assert run_config["ports"] == {}
    assert "restart_policy" in run_config

    image_tag = docker_client.stop_commit_remove(name)
    client = docker_client.get_client()
    with pytest.raises(Exception):
        client.containers.get(name)  # removed, per stop_commit_remove's contract

    tar_bytes = docker_client.save_image(image_tag)
    assert len(tar_bytes) > 0

    loaded_image = docker_client.load_image(tar_bytes)
    new_container = docker_client.run_container(loaded_image.id, name, run_config)
    try:
        assert docker_client.wait_until_running(new_container) is True
        new_container.reload()
        assert new_container.status == "running"
    finally:
        new_container.remove(force=True)
        docker_client.remove_image(image_tag)
