import io

from docker.errors import NotFound

from app.routes import migrate as migrate_route


def _register_destination(client, node_id="regA-c1-edge2", url="http://localhost:9999"):
    client.app.state.registry.mark_registered(node_id, url, "regA", "c1")


def test_migrate_out_unknown_destination_is_400(client):
    resp = client.post(
        "/migrate-out", json={"container_name": "my-app", "destination_node": "regA-c1-edge9"}
    )
    assert resp.status_code == 400


def test_migrate_out_container_not_found_is_404(client, monkeypatch):
    _register_destination(client)

    def fake_get_run_config(name):
        raise NotFound("no such container")

    monkeypatch.setattr(migrate_route.docker_client, "get_run_config", fake_get_run_config)

    resp = client.post(
        "/migrate-out", json={"container_name": "missing", "destination_node": "regA-c1-edge2"}
    )
    assert resp.status_code == 404


def test_migrate_out_success(client, monkeypatch):
    _register_destination(client)

    monkeypatch.setattr(migrate_route.docker_client, "get_run_config", lambda name: {"ports": {}, "restart_policy": {"Name": "no"}})
    monkeypatch.setattr(migrate_route.docker_client, "stop_commit_remove", lambda name: "chameleon-migrate/my-app:abc123")
    monkeypatch.setattr(migrate_route.docker_client, "save_image", lambda tag: b"fake-tar-bytes")

    removed = {}
    monkeypatch.setattr(migrate_route.docker_client, "remove_image", lambda tag: removed.setdefault("tag", tag))

    async def fake_post_multipart(url, files, data):
        return {"status": "running", "container_name": "my-app", "node_id": "regA-c1-edge2"}

    monkeypatch.setattr(migrate_route, "post_multipart", fake_post_multipart)

    resp = client.post(
        "/migrate-out", json={"container_name": "my-app", "destination_node": "regA-c1-edge2"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "migrated"
    assert body["destination_node"] == "regA-c1-edge2"
    assert removed["tag"] == "chameleon-migrate/my-app:abc123"


def test_migrate_out_unreachable_destination_rolls_back(client, monkeypatch):
    _register_destination(client)

    monkeypatch.setattr(migrate_route.docker_client, "get_run_config", lambda name: {"ports": {}, "restart_policy": {"Name": "no"}})
    monkeypatch.setattr(migrate_route.docker_client, "stop_commit_remove", lambda name: "chameleon-migrate/my-app:abc123")
    monkeypatch.setattr(migrate_route.docker_client, "save_image", lambda tag: b"fake-tar-bytes")

    async def fake_post_multipart(url, files, data):
        return None

    monkeypatch.setattr(migrate_route, "post_multipart", fake_post_multipart)

    ran = {}

    def fake_run_container(image, container_name, run_config):
        ran["called"] = True
        return object()

    monkeypatch.setattr(migrate_route.docker_client, "run_container", fake_run_container)
    monkeypatch.setattr(migrate_route.docker_client, "wait_until_running", lambda container, **kw: True)

    resp = client.post(
        "/migrate-out", json={"container_name": "my-app", "destination_node": "regA-c1-edge2"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "failed_rolled_back"
    assert ran["called"] is True


def test_migrate_in_success(client, monkeypatch):
    class FakeImage:
        id = "sha256:fake"

    monkeypatch.setattr(migrate_route.docker_client, "load_image", lambda tar_bytes: FakeImage())
    monkeypatch.setattr(migrate_route.docker_client, "run_container", lambda image, name, run_config: object())
    monkeypatch.setattr(migrate_route.docker_client, "wait_until_running", lambda container, **kw: True)

    resp = client.post(
        "/migrate-in",
        files={"file": ("my-app.tar", io.BytesIO(b"fake-tar-bytes"), "application/x-tar")},
        data={"metadata": '{"container_name": "my-app", "run_config": {"ports": {}, "restart_policy": {"Name": "no"}}}'},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "running"
    assert body["container_name"] == "my-app"


def test_migrate_in_unhealthy_is_500(client, monkeypatch):
    class FakeImage:
        id = "sha256:fake"

    monkeypatch.setattr(migrate_route.docker_client, "load_image", lambda tar_bytes: FakeImage())
    monkeypatch.setattr(migrate_route.docker_client, "run_container", lambda image, name, run_config: object())
    monkeypatch.setattr(migrate_route.docker_client, "wait_until_running", lambda container, **kw: False)

    resp = client.post(
        "/migrate-in",
        files={"file": ("my-app.tar", io.BytesIO(b"fake-tar-bytes"), "application/x-tar")},
        data={"metadata": '{"container_name": "my-app", "run_config": {}}'},
    )
    assert resp.status_code == 500
