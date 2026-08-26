import asyncio

import pytest

from app import scheduler
from app.config import settings
from app.neighbors import NeighborConfig, PeerRegistry


@pytest.mark.asyncio
async def test_scheduler_loop_survives_a_bad_tick(monkeypatch):
    """Regression test: a real run found that an unhandled exception from
    scheduler_tick (a Docker API error, in that case) silently killed the
    scheduler's background loop for good — every other background loop in
    this codebase fails safe and keeps going; this one has to as well.
    """
    registry = PeerRegistry([])
    tracker = scheduler.OverloadTracker()

    calls = []

    async def flaky_tick(r, t):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("simulated Docker API error")

    monkeypatch.setattr(scheduler, "scheduler_tick", flaky_tick)
    monkeypatch.setattr(settings, "SCHEDULER_INTERVAL_SECONDS", 0)

    task = asyncio.create_task(scheduler.run_scheduler_loop(registry, tracker))
    for _ in range(50):
        if len(calls) >= 3:
            break
        await asyncio.sleep(0.01)

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert len(calls) >= 3  # kept ticking after the first tick raised


def test_overload_tracker_resets_on_non_overloaded():
    tracker = scheduler.OverloadTracker()
    assert tracker.record(True) is False
    assert tracker.record(False) is False
    assert tracker.record(True) is False  # counter was reset, this is only #1 again


def test_overload_tracker_fires_exactly_at_sustained_polls(monkeypatch):
    monkeypatch.setattr(settings, "SUSTAINED_POLLS", 3)
    tracker = scheduler.OverloadTracker()
    assert tracker.record(True) is False
    assert tracker.record(True) is False
    assert tracker.record(True) is True
    # fired and reset — next overloaded reading starts counting over
    assert tracker.record(True) is False


@pytest.mark.asyncio
async def test_scheduler_tick_noop_when_no_managed_container(monkeypatch):
    monkeypatch.setattr(settings, "MANAGED_CONTAINER_NAME", "")
    registry = PeerRegistry([])
    tracker = scheduler.OverloadTracker()

    called = {}
    monkeypatch.setattr(scheduler, "_attempt_migration", lambda r: called.setdefault("hit", True))

    await scheduler.scheduler_tick(registry, tracker)
    assert "hit" not in called


@pytest.mark.asyncio
async def test_scheduler_tick_triggers_only_after_sustained_overload(monkeypatch):
    monkeypatch.setattr(settings, "MANAGED_CONTAINER_NAME", "my-app")
    monkeypatch.setattr(settings, "CPU_OVERLOAD_THRESHOLD", 1.0)
    monkeypatch.setattr(settings, "MEM_OVERLOAD_THRESHOLD", 200.0)  # never trips on mem
    monkeypatch.setattr(settings, "SUSTAINED_POLLS", 3)

    monkeypatch.setattr(scheduler.psutil, "cpu_percent", lambda interval=None: 99.0)

    class FakeMem:
        percent = 1.0

    monkeypatch.setattr(scheduler.psutil, "virtual_memory", lambda: FakeMem())

    registry = PeerRegistry([])
    tracker = scheduler.OverloadTracker()

    calls = []

    async def fake_attempt_migration(r):
        calls.append(r)

    monkeypatch.setattr(scheduler, "_attempt_migration", fake_attempt_migration)

    await scheduler.scheduler_tick(registry, tracker)
    await scheduler.scheduler_tick(registry, tracker)
    assert len(calls) == 0
    await scheduler.scheduler_tick(registry, tracker)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_attempt_migration_skips_when_advisor_unreachable(monkeypatch):
    registry = PeerRegistry([NeighborConfig(node_id="regA-c1-edge2", url="http://localhost:9999")])

    async def fake_get_json(url):
        return {
            "node_id": "regA-c1-edge2",
            "timestamp": "2026-08-26T00:00:00Z",
            "cpu_percent": 10,
            "mem_percent": 10,
            "latency_ms": {},
            "history_load_avg_5m": 10,
            "trust_score": 1.0,
        }

    monkeypatch.setattr(scheduler, "get_json", fake_get_json)

    async def fake_post_json(url, body):
        return None

    monkeypatch.setattr(scheduler, "post_json", fake_post_json)

    migrated = {}
    monkeypatch.setattr(
        scheduler, "migrate_container", lambda *a, **kw: migrated.setdefault("hit", True)
    )

    await scheduler._attempt_migration(registry)
    assert "hit" not in migrated


@pytest.mark.asyncio
async def test_attempt_migration_skips_on_recommended_none(monkeypatch):
    registry = PeerRegistry([NeighborConfig(node_id="regA-c1-edge2", url="http://localhost:9999")])

    async def fake_get_json(url):
        return {
            "node_id": "regA-c1-edge2",
            "timestamp": "2026-08-26T00:00:00Z",
            "cpu_percent": 10,
            "mem_percent": 10,
            "latency_ms": {},
            "history_load_avg_5m": 10,
            "trust_score": 1.0,
        }

    async def fake_post_json(url, body):
        return {"recommended_node": "none", "score": 0.0, "reasoning": "nothing qualifies"}

    monkeypatch.setattr(scheduler, "get_json", fake_get_json)
    monkeypatch.setattr(scheduler, "post_json", fake_post_json)

    migrated = {}
    monkeypatch.setattr(
        scheduler, "migrate_container", lambda *a, **kw: migrated.setdefault("hit", True)
    )

    await scheduler._attempt_migration(registry)
    assert "hit" not in migrated


@pytest.mark.asyncio
async def test_attempt_migration_skips_on_recommendation_outside_candidates(monkeypatch):
    registry = PeerRegistry([NeighborConfig(node_id="regA-c1-edge2", url="http://localhost:9999")])

    async def fake_get_json(url):
        return {
            "node_id": "regA-c1-edge2",
            "timestamp": "2026-08-26T00:00:00Z",
            "cpu_percent": 10,
            "mem_percent": 10,
            "latency_ms": {},
            "history_load_avg_5m": 10,
            "trust_score": 1.0,
        }

    async def fake_post_json(url, body):
        # not one of the candidates we sent
        return {"recommended_node": "regA-c1-edge9", "score": 0.9, "reasoning": "made up"}

    monkeypatch.setattr(scheduler, "get_json", fake_get_json)
    monkeypatch.setattr(scheduler, "post_json", fake_post_json)

    migrated = {}
    monkeypatch.setattr(
        scheduler, "migrate_container", lambda *a, **kw: migrated.setdefault("hit", True)
    )

    await scheduler._attempt_migration(registry)
    assert "hit" not in migrated


@pytest.mark.asyncio
async def test_attempt_migration_triggers_on_valid_recommendation(monkeypatch):
    monkeypatch.setattr(settings, "MANAGED_CONTAINER_NAME", "my-app")
    registry = PeerRegistry([NeighborConfig(node_id="regA-c1-edge2", url="http://localhost:9999")])

    async def fake_get_json(url):
        return {
            "node_id": "regA-c1-edge2",
            "timestamp": "2026-08-26T00:00:00Z",
            "cpu_percent": 10,
            "mem_percent": 10,
            "latency_ms": {},
            "history_load_avg_5m": 10,
            "trust_score": 1.0,
        }

    async def fake_post_json(url, body):
        return {"recommended_node": "regA-c1-edge2", "score": 0.9, "reasoning": "lowest load"}

    monkeypatch.setattr(scheduler, "get_json", fake_get_json)
    monkeypatch.setattr(scheduler, "post_json", fake_post_json)

    calls = {}

    async def fake_migrate_container(registry_arg, container_name, destination_node):
        calls["args"] = (container_name, destination_node)
        from app.models import MigrateOutResponse

        return MigrateOutResponse(status="migrated", container_name=container_name, destination_node=destination_node)

    monkeypatch.setattr(scheduler, "migrate_container", fake_migrate_container)

    await scheduler._attempt_migration(registry)
    assert calls["args"] == ("my-app", "regA-c1-edge2")


@pytest.mark.asyncio
async def test_gather_candidate_stats_excludes_unreachable_and_malformed(monkeypatch):
    registry = PeerRegistry(
        [
            NeighborConfig(node_id="regA-c1-edge2", url="http://localhost:9998"),
            NeighborConfig(node_id="regA-c1-edge3", url="http://localhost:9999"),
            NeighborConfig(node_id="regA-c1-edge4", url="http://localhost:9997"),
        ]
    )

    async def fake_get_json(url):
        if "9998" in url:
            return {
                "node_id": "regA-c1-edge2",
                "timestamp": "2026-08-26T00:00:00Z",
                "cpu_percent": 10,
                "mem_percent": 10,
                "latency_ms": {},
                "history_load_avg_5m": 10,
                "trust_score": 1.0,
            }
        if "9999" in url:
            return None  # unreachable
        return {"bogus": "shape"}  # malformed

    monkeypatch.setattr(scheduler, "get_json", fake_get_json)

    candidates = await scheduler._gather_candidate_stats(registry)
    assert [c.node_id for c in candidates] == ["regA-c1-edge2"]
