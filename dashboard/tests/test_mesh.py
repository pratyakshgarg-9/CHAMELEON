import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mesh import MeshState

E1, E2, E3 = "regA-c1-edge1", "regA-c1-edge2", "regA-c1-edge3"
T0 = datetime(2026, 10, 8, 12, 0, 0, tzinfo=timezone.utc)


def snap(node_id, leader=E3, boot="b1", latest_seq=0, events=(), containers=(), neighbors=(), cpu=10.0, mem=50.0):
    return {
        "node_id": node_id,
        "region": "regA",
        "cluster": "c1",
        "boot_id": boot,
        "uptime_seconds": 100,
        "leader_node_id": leader,
        "stats": {"cpu_percent": cpu, "mem_percent": mem, "history_load_avg_5m": 8.0, "trust_score": 0.5},
        "neighbors": list(neighbors),
        "containers": [{"name": c, "id": "x", "image": "img"} for c in containers],
        "events": list(events),
        "latest_seq": latest_seq,
    }


def mesh():
    return MeshState({E1: "u1", E2: "u2", E3: "u3"})


def rounds(m, results, n=1, start=0):
    """Feed the same poll result n times, 2s apart."""
    for i in range(n):
        m.update(results, T0 + timedelta(seconds=2 * (start + i)))


def healthy():
    return {E1: snap(E1), E2: snap(E2), E3: snap(E3)}


def types(m):
    return [e["type"] for e in sorted(m.events, key=lambda e: e["id"])]


def test_backlog_from_before_we_started_watching_is_not_news():
    m = mesh()
    old = {"seq": 1, "ts": "2026-10-07T10:00:00.000Z", "node_id": E1, "type": "migration_completed", "message": "old"}
    rounds(m, {E1: snap(E1, latest_seq=1, events=[old]), E2: snap(E2), E3: snap(E3)})
    assert not any(e["message"] == "old" for e in m.events)
    assert m.cursor(E1) == 1


def test_new_node_events_are_merged_and_hidden_types_filtered():
    m = mesh()
    rounds(m, healthy())
    evs = [
        {"seq": 1, "ts": "2026-10-08T12:00:05.000Z", "node_id": E1, "type": "migration_completed", "message": "m"},
        {"seq": 2, "ts": "2026-10-08T12:00:06.000Z", "node_id": E1, "type": "node_started", "message": "hidden"},
        {"seq": 3, "ts": "2026-10-08T12:00:07.000Z", "node_id": E2, "type": "migration_received", "message": "hidden"},
    ]
    res = healthy()
    res[E1] = snap(E1, latest_seq=3, events=evs)
    rounds(m, res, start=1)
    assert [e["message"] for e in m.events if e["source"] == "node"] == ["m"]
    assert m.migrations == 1
    assert m.cursor(E1) == 3


def test_full_node_ids_in_node_messages_are_shortened():
    m = mesh()
    rounds(m, healthy())
    ev = {"seq": 1, "ts": "2026-10-08T12:00:05.000Z", "node_id": E2, "type": "leader_elected",
          "message": f"{E2} won the election and is now leader"}
    res = healthy()
    res[E2] = snap(E2, latest_seq=1, events=[ev])
    rounds(m, res, start=1)
    assert [e["message"] for e in m.events if e["source"] == "node"] == ["edge2 won the election and is now leader"]


def test_node_down_needs_two_misses_and_recovery_is_announced():
    m = mesh()
    rounds(m, healthy(), n=2)
    down = {E1: snap(E1), E2: snap(E2), E3: None}
    rounds(m, down, start=2)
    assert "node_down" not in types(m)  # one miss isn't an outage
    rounds(m, down, start=3)
    assert types(m).count("node_down") == 1
    rounds(m, down, n=3, start=4)
    assert types(m).count("node_down") == 1  # not repeated while it stays down
    rounds(m, healthy(), start=7)
    assert types(m).count("node_up") == 1
    view = {n["node_id"]: n for n in m.snapshot(T0)["nodes"]}
    assert view[E3]["status"] == "up"


def test_back_online_is_stamped_when_the_agent_started_not_when_we_noticed():
    m = mesh()
    rounds(m, healthy(), n=2)
    rounds(m, {E1: snap(E1), E2: snap(E2), E3: None}, n=2, start=2)  # down, estimated at +3s
    back = healthy()
    back[E3] = dict(snap(E3, boot="b2"), uptime_seconds=2)  # agent up for 2s when polled at +12s
    rounds(m, back, start=6)
    up = next(e for e in m.events if e["type"] == "node_up")
    assert up["ts"] == "2026-10-08T12:00:10.000Z"


def test_node_unreachable_since_startup_is_not_reported_as_dying():
    m = mesh()
    rounds(m, {E1: snap(E1), E2: snap(E2), E3: None}, n=4)
    assert "node_down" not in types(m)
    assert {n["node_id"]: n["status"] for n in m.snapshot(T0)["nodes"]}[E3] == "down"


def test_leader_selected_after_two_agreeing_rounds():
    m = mesh()
    rounds(m, healthy())
    assert "leader_selected" not in types(m)
    rounds(m, healthy(), start=1)
    ev = [e for e in m.events if e["type"] == "leader_selected"]
    assert len(ev) == 1 and "edge3 selected as leader" in ev[0]["message"]
    c = m.snapshot(T0)["cluster"]
    assert (c["leader"], c["status"], c["agree"]) == (E3, "healthy", 3)


def test_leader_death_is_lost_then_replaced():
    m = mesh()
    rounds(m, healthy(), n=3)
    # leader dies; survivors still (stale) name it
    stale = {E1: snap(E1, leader=E3), E2: snap(E2, leader=E3), E3: None}
    rounds(m, stale, n=2, start=3)
    assert types(m)[-2:] == ["node_down", "leader_lost"]
    assert m.snapshot(T0)["cluster"]["status"] == "re-electing"
    assert m.snapshot(T0)["cluster"]["leader"] is None
    # edge2 wins and announces; edge1 follows
    new = {E1: snap(E1, leader=E2), E2: snap(E2, leader=E2), E3: None}
    rounds(m, new, n=2, start=5)
    sel = [e for e in m.events if e["type"] == "leader_selected"]
    assert sel[-1]["message"] == "New leader selected: edge2 (was edge3)"
    c = m.snapshot(T0)["cluster"]
    assert (c["leader"], c["status"]) == (E2, "degraded")


def test_node_down_is_stamped_when_it_died_not_when_we_noticed():
    m = mesh()
    rounds(m, healthy(), n=2)  # last answer at T0+2s
    rounds(m, {E1: snap(E1), E2: snap(E2), E3: None}, n=2, start=2)  # misses at +4s, +6s -> detected at +6s
    down = next(e for e in m.events if e["type"] == "node_down")
    assert down["ts"] == "2026-10-08T12:00:03.000Z"  # midpoint of last answer and first miss


def test_leader_lost_is_logged_even_if_survivors_already_replaced_it():
    # Survivors can elect a new leader before the dashboard has noticed the
    # old one is gone — the log must still read: died, lost, selected.
    m = mesh()
    rounds(m, healthy(), n=3)
    replaced = {E1: snap(E1, leader=E2), E2: snap(E2, leader=E2), E3: None}
    rounds(m, replaced, n=4, start=3)
    ordered = [e for e in sorted(m.events, key=lambda e: e["id"]) if e["type"] in ("node_down", "leader_lost", "leader_selected")]
    assert [e["type"] for e in ordered] == ["leader_selected", "node_down", "leader_lost", "leader_selected"]
    down = next(e for e in m.events if e["type"] == "node_down")
    lost = next(e for e in m.events if e["type"] == "leader_lost")
    assert down["ts"] == lost["ts"]
    assert ordered[-1]["message"] == "New leader selected: edge2 (was edge3)"


def test_split_votes_do_not_spam_leader_changes():
    m = mesh()
    rounds(m, healthy(), n=3)
    flap = {E1: snap(E1, leader=E1), E2: snap(E2), E3: snap(E3)}
    rounds(m, flap, n=1, start=3)  # one round of disagreement...
    rounds(m, healthy(), n=2, start=4)  # ...then everyone agrees again
    assert len([e for e in m.events if e["type"] == "leader_selected"]) == 1


def test_restart_between_polls_is_detected_by_boot_id():
    m = mesh()
    rounds(m, healthy(), n=2)
    res = healthy()
    res[E2] = snap(E2, boot="b2", latest_seq=2)
    rounds(m, res, start=2)
    assert [e["message"] for e in m.events if e["type"] == "node_up"] == ["edge2 restarted"]
    assert m.cursor(E2) == 0  # new process counts from 1 again; next poll picks its events up


def test_container_placement_follows_a_migration_and_shows_in_transit():
    m = mesh()
    rounds(m, {E1: snap(E1, containers=["demo-app"]), E2: snap(E2), E3: snap(E3)})
    c = m.snapshot(T0)["containers"][0]
    assert (c["name"], c["node"], c["state"], c["history"]) == ("demo-app", E1, "running", ["edge1"])

    # mid-migration: gone from edge1, not yet on edge2
    rounds(m, healthy(), start=1)
    assert m.snapshot(T0 + timedelta(seconds=3))["containers"][0]["state"] == "in-transit"

    rounds(m, {E1: snap(E1), E2: snap(E2, containers=["demo-app"]), E3: snap(E3)}, start=2)
    c = m.snapshot(T0 + timedelta(seconds=5))["containers"][0]
    assert (c["node"], c["state"], c["history"], c["moves"]) == (E2, "running", ["edge1", "edge2"], 1)


def test_container_on_a_dead_node_is_flagged_not_forgotten():
    m = mesh()
    rounds(m, {E1: snap(E1, containers=["demo-app"]), E2: snap(E2), E3: snap(E3)})
    rounds(m, {E1: None, E2: snap(E2), E3: snap(E3)}, n=3, start=1)
    c = m.snapshot(T0 + timedelta(seconds=10))["containers"][0]
    assert (c["node"], c["state"]) == (E1, "node-down")


def test_cluster_aggregates_latency_only_between_live_nodes():
    m = mesh()
    nb = lambda a, b: [{"node_id": a, "latency_ms": 10.0}, {"node_id": b, "latency_ms": 30.0}]
    rounds(m, {E1: snap(E1, neighbors=nb(E2, E3)), E2: snap(E2, neighbors=nb(E1, E3)), E3: snap(E3, neighbors=nb(E1, E2))})
    assert m.snapshot(T0)["cluster"]["avg_latency_ms"] == 20.0
    view = {n["node_id"]: n for n in m.snapshot(T0)["nodes"]}
    assert [x["short"] for x in view[E1]["neighbors"]] == ["edge2", "edge3"]
