import pytest

from app import election
from app.config import settings
from app.neighbors import NeighborConfig, PeerRegistry


def _registry_with_peers(*peers):
    registry = PeerRegistry([NeighborConfig(node_id=p[0], url=p[1]) for p in peers])
    for node_id, url, region, cluster in peers:
        registry.mark_registered(node_id, url, region, cluster)
    return registry


def test_cluster_peers_filters_by_region_and_cluster():
    registry = _registry_with_peers(
        ("regA-c1-edge2", "http://localhost:8001", "regA", "c1"),
        ("regA-c2-edge3", "http://localhost:8002", "regA", "c2"),  # different cluster
        ("regB-c1-edge4", "http://localhost:8003", "regB", "c1"),  # different region
    )
    peers = election._cluster_peers(registry)
    assert [p.node_id for p in peers] == ["regA-c1-edge2"]


@pytest.mark.asyncio
async def test_start_election_becomes_leader_when_no_higher_peers(monkeypatch):
    monkeypatch.setattr(settings, "NODE_ID", "regA-c1-edge5")
    registry = _registry_with_peers(("regA-c1-edge2", "http://localhost:8001", "regA", "c1"))
    state = election.ElectionState()

    announced = []

    async def fake_post_json(url, body):
        announced.append(url)
        return {"status": "ack"}

    monkeypatch.setattr(election, "post_json", fake_post_json)

    await election.start_election(registry, state)

    assert state.current_leader == "regA-c1-edge5"
    assert announced == ["http://localhost:8001/coordinator"]


@pytest.mark.asyncio
async def test_start_election_defers_when_higher_peer_alive(monkeypatch):
    monkeypatch.setattr(settings, "NODE_ID", "regA-c1-edge1")
    registry = _registry_with_peers(("regA-c1-edge2", "http://localhost:8001", "regA", "c1"))
    state = election.ElectionState()

    async def fake_post_json(url, body):
        return {"status": "ok"}  # higher peer is alive

    monkeypatch.setattr(election, "post_json", fake_post_json)

    await election.start_election(registry, state)

    assert state.current_leader is None


@pytest.mark.asyncio
async def test_start_election_becomes_leader_when_higher_peer_unreachable(monkeypatch):
    monkeypatch.setattr(settings, "NODE_ID", "regA-c1-edge1")
    registry = _registry_with_peers(("regA-c1-edge2", "http://localhost:8001", "regA", "c1"))
    state = election.ElectionState()

    async def fake_post_json(url, body):
        return None  # unreachable

    monkeypatch.setattr(election, "post_json", fake_post_json)

    await election.start_election(registry, state)

    assert state.current_leader == "regA-c1-edge1"


@pytest.mark.asyncio
async def test_start_election_skips_when_already_in_progress(monkeypatch):
    registry = _registry_with_peers(("regA-c1-edge2", "http://localhost:8001", "regA", "c1"))
    state = election.ElectionState()
    state.election_in_progress = True

    called = {}

    async def fake_post_json(url, body):
        called["hit"] = True
        return None

    monkeypatch.setattr(election, "post_json", fake_post_json)

    await election.start_election(registry, state)

    assert "hit" not in called
    assert state.current_leader is None


@pytest.mark.asyncio
async def test_check_leader_liveness_still_reverifies_when_self_is_leader(monkeypatch):
    # Regression test: a live 3-node run showed that if a self-declared
    # leader stops re-checking, a startup race where multiple nodes each
    # briefly self-elect (before they've learned about each other) never
    # heals — every node stays convinced it's the leader forever. The fix
    # is that check_leader_liveness always re-runs start_election, even
    # when this node already believes itself the leader.
    registry = PeerRegistry([])
    state = election.ElectionState()
    state.current_leader = settings.NODE_ID

    calls = []

    async def fake_start_election(r, s):
        calls.append(1)

    monkeypatch.setattr(election, "start_election", fake_start_election)

    await election.check_leader_liveness(registry, state)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_check_leader_liveness_triggers_election_when_no_leader(monkeypatch):
    registry = PeerRegistry([])
    state = election.ElectionState()

    calls = []

    async def fake_start_election(r, s):
        calls.append(1)

    monkeypatch.setattr(election, "start_election", fake_start_election)

    await election.check_leader_liveness(registry, state)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_check_leader_liveness_triggers_election_when_leader_stale(monkeypatch):
    from datetime import datetime, timedelta, timezone

    registry = _registry_with_peers(("regA-c1-edge2", "http://localhost:8001", "regA", "c1"))
    peer = registry.list_all()[0]
    peer.last_seen = datetime.now(timezone.utc) - timedelta(hours=1)

    state = election.ElectionState()
    state.current_leader = "regA-c1-edge2"

    calls = []

    async def fake_start_election(r, s):
        calls.append(1)

    monkeypatch.setattr(election, "start_election", fake_start_election)

    await election.check_leader_liveness(registry, state)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_check_leader_liveness_still_calls_start_election_when_leader_fresh(monkeypatch):
    # start_election itself is what decides to defer to a live leader (it
    # pings higher peers) — check_leader_liveness's job is just to trigger
    # that check every tick, fresh leader or not, per the self-healing
    # rationale above.
    registry = _registry_with_peers(("regA-c1-edge2", "http://localhost:8001", "regA", "c1"))
    state = election.ElectionState()
    state.current_leader = "regA-c1-edge2"  # mark_registered already set last_seen = now

    calls = []

    async def fake_start_election(r, s):
        calls.append(1)

    monkeypatch.setattr(election, "start_election", fake_start_election)

    await election.check_leader_liveness(registry, state)
    assert len(calls) == 1


def test_election_route_returns_ok_immediately(client, monkeypatch):
    # The response must not wait on the fire-and-forget follow-up election —
    # stub it out so this test isn't at the mercy of real network calls.
    async def fake_start_election(r, s):
        pass

    from app.routes import election as election_route

    monkeypatch.setattr(election_route, "start_election", fake_start_election)

    resp = client.post("/election", json={"from_node_id": "regA-c1-edge2"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_coordinator_route_updates_state(client):
    resp = client.post("/coordinator", json={"leader_node_id": "regA-c1-edge2"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "ack"

    leader_resp = client.get("/leader")
    assert leader_resp.json()["leader_node_id"] == "regA-c1-edge2"


def test_leader_route_reachable(client):
    # With no configured neighbors, the real background election monitor
    # legitimately self-elects almost immediately (a single-node "cluster"
    # trivially wins) — so the only thing guaranteed here is that the
    # endpoint responds with either "no leader yet" or "it's me".
    resp = client.get("/leader")
    assert resp.status_code == 200
    assert resp.json()["leader_node_id"] in (None, settings.NODE_ID)
