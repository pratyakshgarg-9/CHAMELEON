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


def test_run_container_is_idempotent_on_name_conflict():
    """Regression test: a client-side timeout followed by a retry against a
    server that actually succeeded must not crash with an unhandled 409 —
    run_container has to replace whatever's already running under that
    name, since the incoming image is authoritative during a migration.
    Uses a real committed image (sleep 300 baked into its CMD) so the
    recreated container actually stays running, same as a real migration.
    """
    client = docker_client.get_client()
    seed_name = f"chameleon-test-seed-{uuid.uuid4().hex[:8]}"
    target_name = f"chameleon-test-{uuid.uuid4().hex[:8]}"

    seed = client.containers.run("busybox", "sleep 300", name=seed_name, detach=True)
    image_tag = docker_client.stop_commit_remove(seed_name)
    run_config = {"ports": {}, "restart_policy": None}

    try:
        first = docker_client.run_container(image_tag, target_name, run_config)
        first_id = first.id

        second = docker_client.run_container(image_tag, target_name, run_config)
        try:
            assert docker_client.wait_until_running(second) is True
            assert second.id != first_id
            with pytest.raises(Exception):
                client.containers.get(first_id)  # replaced, not left dangling
        finally:
            second.remove(force=True)
    finally:
        docker_client.remove_image(image_tag)
        try:
            client.containers.get(target_name).remove(force=True)
        except Exception:
            pass
