import pytest

from app import deps, events, migration
from app.config import settings
from app.neighbors import NeighborConfig, PeerRegistry
from app.routes import status_route


def test_event_log_sequences_and_cursor():
    log = events.EventLog()
    a = log.record("x", "first")
    b = log.record("y", "second", k=1)
    assert (a["seq"], b["seq"]) == (1, 2)
    assert [e["type"] for e in log.since(1)] == ["y"]
    assert log.since(2) == []
    assert log.latest_seq == 2
    assert b["data"] == {"k": 1}
    assert b["ts"].endswith("Z")


def test_event_log_is_bounded_but_seq_keeps_counting():
    log = events.EventLog(maxlen=3)
    for i in range(5):
        log.record("t", f"e{i}")
    assert [e["seq"] for e in log.since(0)] == [3, 4, 5]
    assert log.latest_seq == 5


def test_status_bundles_node_state_and_events_since_cursor(client, monkeypatch):
    monkeypatch.setattr(status_route.docker_client, "list_app_containers", lambda: [{"name": "demo-app", "id": "abc"}])
    before = events.event_log.latest_seq
    events.record("leader_elected", "test event")

    body = client.get("/status", params={"since": before}).json()

    assert body["node_id"] == settings.NODE_ID
    assert body["boot_id"] == events.BOOT_ID
    assert {"cpu_percent", "mem_percent", "trust_score"} <= set(body["stats"])
    assert body["containers"] == [{"name": "demo-app", "id": "abc"}]
    assert body["containers_error"] is None
    assert [e["message"] for e in body["events"]] == ["test event"]
    assert body["latest_seq"] == events.event_log.latest_seq


def test_status_survives_docker_being_unavailable(client, monkeypatch):
    def boom():
        raise RuntimeError("docker daemon not reachable")

    monkeypatch.setattr(status_route.docker_client, "list_app_containers", boom)

    body = client.get("/status").json()

    assert body["containers"] == []
    assert "docker daemon not reachable" in body["containers_error"]
    assert body["stats"]["node_id"] == settings.NODE_ID


@pytest.mark.asyncio
async def test_status_path_does_not_wait_on_a_slow_trust_service(monkeypatch):
    # If /status blocked on trust-service, a slow trust-service would make
    # every healthy node look DOWN on the dashboard.
    import asyncio
    import time

    from app import stats

    async def very_slow():
        await asyncio.sleep(10)
        return 0.9

    monkeypatch.setattr(stats, "_fetch_trust_score", very_slow)
    monkeypatch.setattr(stats, "_trust_cache", None)

    start = time.monotonic()
    payload = await stats.build_stats_payload(PeerRegistry([]), cached_trust=True)

    assert time.monotonic() - start < 3
    assert payload.trust_score == 1.0  # same fail-open default as an unreachable trust-service


@pytest.mark.asyncio
async def test_cn_mismatch_records_an_event(monkeypatch):
    monkeypatch.setattr(settings, "MTLS_ENABLED", True)

    async def fake_post_json(url, body):
        return {"status": "ok"}

    monkeypatch.setattr(deps, "post_json", fake_post_json)
    before = events.event_log.latest_seq

    with pytest.raises(Exception):
        await deps.require_cn_matches("regA-c1-edge2", "attacker")

    new = events.event_log.since(before)
    assert [e["type"] for e in new] == ["cn_rejected"]
    assert new[0]["data"] == {"claimed": "regA-c1-edge2", "cn": "attacker"}


def _fake_migration_docker(monkeypatch):
    monkeypatch.setattr(migration.docker_client, "get_run_config", lambda name: {"ports": {}, "volumes": {}})
    monkeypatch.setattr(migration.docker_client, "stop_commit_remove", lambda name: "img:tag")
    monkeypatch.setattr(migration.docker_client, "save_image", lambda tag: b"tar")
    monkeypatch.setattr(migration.docker_client, "remove_image", lambda tag: None)


@pytest.mark.asyncio
async def test_successful_migration_records_started_then_completed(monkeypatch):
    _fake_migration_docker(monkeypatch)
    sent = {}

    async def fake_post_multipart(url, files, data):
        sent["data"] = data
        return {"status": "running"}

    monkeypatch.setattr(migration, "post_multipart", fake_post_multipart)
    registry = PeerRegistry([NeighborConfig(node_id="regA-c1-edge2", url="http://x")])
    before = events.event_log.latest_seq

    await migration.migrate_container(registry, "demo-app", "regA-c1-edge2")

    new = events.event_log.since(before)
    assert [e["type"] for e in new] == ["migration_started", "migration_completed"]
    assert new[1]["data"] == {"container": "demo-app", "source": settings.NODE_ID, "destination": "regA-c1-edge2"}
    # the destination needs to know who sent it, for its own migration_received event
    assert f'"source_node": "{settings.NODE_ID}"' in sent["data"]["metadata"]


@pytest.mark.asyncio
async def test_failed_migration_records_failure_not_completion(monkeypatch):
    _fake_migration_docker(monkeypatch)

    async def fake_post_multipart(url, files, data):
        return None

    monkeypatch.setattr(migration, "post_multipart", fake_post_multipart)
    monkeypatch.setattr(migration.docker_client, "run_container", lambda *a, **k: object())
    monkeypatch.setattr(migration.docker_client, "wait_until_running", lambda c: True)
    registry = PeerRegistry([NeighborConfig(node_id="regA-c1-edge2", url="http://x")])
    before = events.event_log.latest_seq

    await migration.migrate_container(registry, "demo-app", "regA-c1-edge2")

    assert [e["type"] for e in events.event_log.since(before)] == ["migration_started", "migration_failed"]
