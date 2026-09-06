import time
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


def test_stateful_migration_preserves_volume_data():
    """The requirement, made concrete: write a test file into the managed
    container's volume, run the full export -> stop/commit -> import ->
    recreate sequence (the real /migrate-out -> /migrate-in mechanics, just
    called directly instead of over HTTP), and confirm the file survives
    unchanged on the "destination" side.

    Deliberately does NOT write the marker as part of the container's own
    startup command (e.g. `sh -c "echo ... > /data/testfile.txt && sleep
    300"`) — that command gets baked into the committed image and would
    re-execute (rewriting the same file) every time the image is used to
    start a new container, which would make this test pass regardless of
    whether the volume-restore mechanism actually works. An earlier version
    of this test had exactly that bug and passed locally while the real
    mechanism underneath it was broken (a get_archive/put_archive path
    nesting issue, only caught by cross-machine AWS testing — see
    export_volume_data's comment). Writing the marker via a separate
    exec_run, with a static `sleep` as the container's own command, means
    recreating the container from the committed image does nothing to
    /data on its own — only import_volume_data can make the file reappear.
    """
    client = docker_client.get_client()
    name = f"chameleon-test-{uuid.uuid4().hex[:8]}"
    volume_name = f"chameleon-test-vol-{uuid.uuid4().hex[:8]}"
    marker = f"hello from {uuid.uuid4().hex[:8]}"

    container = client.containers.run(
        "busybox",
        "sleep 300",
        name=name,
        detach=True,
        volumes={volume_name: {"bind": "/data", "mode": "rw"}},
    )
    try:
        # give the container a moment to actually be running before we exec into it
        for _ in range(20):
            container.reload()
            if container.status == "running":
                break
            time.sleep(0.1)

        exit_code, _ = container.exec_run(f"sh -c \"echo '{marker}' > /data/testfile.txt\"")
        assert exit_code == 0

        run_config = docker_client.get_run_config(name)
        assert run_config["volumes"] == {volume_name: {"bind": "/data", "mode": "rw"}}

        volume_tar = docker_client.export_volume_data(volume_name)
        assert len(volume_tar) > 0

        image_tag = docker_client.stop_commit_remove(name)

        # "destination" volume — a different name, same as a real cross-node
        # migration would use, to prove this isn't just reading the same volume back
        dest_volume_name = f"chameleon-test-vol-dest-{uuid.uuid4().hex[:8]}"
        docker_client.import_volume_data(dest_volume_name, volume_tar)

        dest_run_config = {
            "ports": run_config["ports"],
            "restart_policy": run_config["restart_policy"],
            "volumes": {dest_volume_name: {"bind": "/data", "mode": "rw"}},
        }
        new_container = docker_client.run_container(image_tag, name, dest_run_config)
        try:
            assert docker_client.wait_until_running(new_container) is True
            exit_code, output = new_container.exec_run("cat /data/testfile.txt")
            assert exit_code == 0
            assert output.decode().strip() == marker
        finally:
            new_container.remove(force=True)
            docker_client.remove_image(image_tag)
            client.volumes.get(dest_volume_name).remove(force=True)
    finally:
        try:
            client.containers.get(name).remove(force=True)
        except Exception:
            pass
        try:
            client.volumes.get(volume_name).remove(force=True)
        except Exception:
            pass


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
