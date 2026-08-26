import asyncio

import pytest

from app import heartbeat_loop
from app.neighbors import NeighborConfig, PeerRegistry


def _registry_with_one_peer() -> PeerRegistry:
    return PeerRegistry([NeighborConfig(node_id="regA-c1-edge2", url="http://localhost:9999")])


@pytest.mark.asyncio
async def test_heartbeat_once_records_latency_on_success(monkeypatch):
    registry = _registry_with_one_peer()

    async def fake_post_json(url, json_body):
        await asyncio.sleep(0.02)
        return {"node_id": "regA-c1-edge2", "timestamp": "2026-08-26T00:00:00Z", "status": "alive"}

    monkeypatch.setattr(heartbeat_loop, "post_json", fake_post_json)

    await heartbeat_loop.heartbeat_once(registry)

    peer = registry.list_all()[0]
    assert peer.latency_ms is not None
    # Loose floor, not ~20ms exactly — sleep()/perf_counter() granularity
    # can land a hair under the nominal delay depending on OS scheduling.
    assert peer.latency_ms >= 10
    assert peer.last_seen is not None


@pytest.mark.asyncio
async def test_heartbeat_once_leaves_latency_none_on_failure(monkeypatch):
    registry = _registry_with_one_peer()

    async def fake_post_json(url, json_body):
        return None

    monkeypatch.setattr(heartbeat_loop, "post_json", fake_post_json)

    await heartbeat_loop.heartbeat_once(registry)

    peer = registry.list_all()[0]
    assert peer.latency_ms is None


@pytest.mark.asyncio
async def test_heartbeat_once_no_peers_is_a_noop():
    registry = PeerRegistry([])
    await heartbeat_loop.heartbeat_once(registry)
    assert registry.list_all() == []
