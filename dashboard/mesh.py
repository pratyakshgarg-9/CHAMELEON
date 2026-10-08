"""Pure state machine behind the dashboard: takes the latest poll result for
each node and turns it into (a) what to draw and (b) a merged event log.
No network and no clock of its own — update() is handed `now`, so every
transition (node down, leader lost, container moved) is unit-testable.

Division of labor for events: nodes report what only they can know
(election won, migration done, overload, rejected identity); this module
derives what only an outside observer can (a node stopped answering, the
cluster's leader changed). A dead node can't announce its own death.
"""

from collections import Counter
from datetime import datetime, timedelta
from typing import Optional

# Recorded by node-agent but redundant for a human reading the log: the
# dashboard's own node-up detection covers restarts, and the source node's
# migration_completed already says everything migration_received would.
_HIDDEN_NODE_EVENTS = {"node_started", "migration_received"}


def short(node_id: str) -> str:
    return node_id.split("-")[-1] if node_id else "?"


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class _Node:
    def __init__(self, node_id: str, url: str):
        self.node_id = node_id
        self.url = url
        self.up: Optional[bool] = None  # None = not heard from yet
        self.fails = 0
        self.seen_up = False
        self.last_ok: Optional[datetime] = None
        self.first_fail: Optional[datetime] = None
        self.down_since: Optional[datetime] = None
        self.down_ts: Optional[datetime] = None  # best estimate of when it actually died
        self.snapshot: Optional[dict] = None
        self.boot_id: Optional[str] = None
        self.cursor: Optional[int] = None


class MeshState:
    def __init__(self, nodes: dict, down_after: int = 2, confirm_rounds: int = 2, max_events: int = 500):
        self.nodes = {nid: _Node(nid, url) for nid, url in nodes.items()}
        self.down_after = down_after
        self.confirm_rounds = confirm_rounds
        self.max_events = max_events
        self.events: list = []
        self.migrations = 0
        self.leader: Optional[str] = None
        self.lost = False
        self._pending: Optional[tuple] = None
        self._votes: Counter = Counter()
        self.placement: dict = {}
        self._next_id = 0

    # ---- inputs -------------------------------------------------------

    def cursor(self, node_id: str) -> int:
        return self.nodes[node_id].cursor or 0

    def update(self, results: dict, now: datetime) -> None:
        """results: node_id -> that node's /status body, or None if it
        didn't answer this round."""
        for node_id, snap in results.items():
            self._ingest(self.nodes[node_id], snap, now)
        self._evaluate_leader(now)
        self._track_containers(now)

    def _emit(self, ts: str, node_id: str, type: str, message: str, source: str) -> None:
        self._next_id += 1
        self.events.append(
            {"id": self._next_id, "ts": ts, "node_id": node_id, "type": type, "message": message, "source": source}
        )
        if len(self.events) > self.max_events:
            del self.events[: len(self.events) - self.max_events]

    def _ingest(self, n: _Node, snap: Optional[dict], now: datetime) -> None:
        if snap is None:
            n.fails += 1
            if n.fails == 1:
                n.first_fail = now
            if n.fails >= self.down_after and n.up is not False:
                was_up = n.up
                n.up = False
                n.down_since = n.first_fail
                # It died somewhere between the last answer and the first miss.
                # Stamping the event with detection time (two misses later)
                # would make the log read as if the cluster reacted before the
                # death — an election "before" the node went down.
                n.down_ts = n.last_ok + (n.first_fail - n.last_ok) / 2 if n.last_ok else n.first_fail
                # Only a real up -> down transition is news; a node that was
                # never reachable since the dashboard started is not "died".
                if was_up:
                    self._emit(iso(n.down_ts), n.node_id, "node_down", f"{short(n.node_id)} stopped responding", "dashboard")
            return

        n.fails = 0
        n.first_fail = None
        restarted = n.boot_id is not None and snap["boot_id"] != n.boot_id
        # The node reports its own uptime, so "came back" can be stamped when
        # the agent actually started rather than when we first heard from it
        # (otherwise its own "won the election" event precedes "back online").
        # Never earlier than when we saw it die.
        started_at = now - timedelta(seconds=snap.get("uptime_seconds") or 0)
        if n.down_ts is not None and started_at < n.down_ts:
            started_at = n.down_ts
        if n.up is False and n.seen_up:
            self._emit(iso(started_at), n.node_id, "node_up", f"{short(n.node_id)} is back online", "dashboard")
        elif restarted:
            self._emit(iso(started_at), n.node_id, "node_up", f"{short(n.node_id)} restarted", "dashboard")

        n.up = True
        n.seen_up = True
        n.last_ok = now
        n.down_since = None
        n.down_ts = None
        n.snapshot = snap
        n.boot_id = snap["boot_id"]

        if restarted:
            # seq restarted at 1 in the new process; this response was asked
            # for with the old cursor, so rewind and pick the new run up next round.
            n.cursor = 0
        elif n.cursor is None:
            n.cursor = snap["latest_seq"]  # history from before we started watching isn't news
        else:
            for e in snap["events"]:
                if e["seq"] > n.cursor:
                    self._add_node_event(e)
            n.cursor = max(n.cursor, snap["latest_seq"])

    def _add_node_event(self, e: dict) -> None:
        if e["type"] in _HIDDEN_NODE_EVENTS:
            return
        if e["type"] == "migration_completed":
            self.migrations += 1
        message = e["message"]
        for node_id in self.nodes:  # nodes write full ids; the page uses the short names everywhere else
            message = message.replace(node_id, short(node_id))
        self._emit(e["ts"], e["node_id"], e["type"], message, "node")

    # ---- derived: leader ----------------------------------------------

    def _evaluate_leader(self, now: datetime) -> None:
        up_nodes = [n for n in self.nodes.values() if n.up and n.snapshot]
        votes: Counter = Counter()
        for n in up_nodes:
            lid = n.snapshot.get("leader_node_id")
            target = self.nodes.get(lid)
            # A leader the dashboard knows is down can't be the live leader,
            # however many (stale) nodes still say so.
            if lid and (target is None or target.up is not False):
                votes[lid] += 1
        self._votes = votes
        top = max(votes.items(), key=lambda kv: (kv[1], kv[0]))[0] if votes else None

        # The leader's own node dying is "leader lost" even when the survivors
        # already elected a replacement by the time we noticed — the log
        # should still tell that story, stamped when it actually died.
        dead = self.nodes.get(self.leader) if self.leader else None
        if dead is not None and dead.up is False and not self.lost:
            self._mark_leader_lost(dead.down_ts or now)

        if top is None:
            self._pending = None
            if self.leader is not None and not self.lost and up_nodes:
                self._mark_leader_lost(now)
        elif top == self.leader:
            self._pending = None
            self.lost = False
        else:
            count = self._pending[1] + 1 if self._pending and self._pending[0] == top else 1
            self._pending = (top, count)
            if count >= self.confirm_rounds:
                old, self.leader, self.lost, self._pending = self.leader, top, False, None
                message = (
                    f"{short(top)} selected as leader"
                    if old is None
                    else f"New leader selected: {short(top)} (was {short(old)})"
                )
                self._emit(iso(now), top, "leader_selected", message, "dashboard")

    def _mark_leader_lost(self, at: datetime) -> None:
        self.lost = True
        self._emit(
            iso(at), self.leader, "leader_lost",
            f"Leader {short(self.leader)} is unreachable — re-election in progress", "dashboard",
        )

    # ---- derived: container placement ---------------------------------

    def _track_containers(self, now: datetime) -> None:
        present: dict = {}
        info: dict = {}
        for n in self.nodes.values():
            if n.up and n.snapshot:
                for c in n.snapshot.get("containers") or []:
                    present.setdefault(c["name"], []).append(n.node_id)
                    info[c["name"]] = c

        for name, where in present.items():
            rec = self.placement.get(name)
            if rec is None:
                rec = self.placement[name] = {"name": name, "node": sorted(where)[0], "history": [], "since": iso(now)}
                rec["history"].append(rec["node"])
            elif rec["node"] not in where:
                rec["node"] = sorted(where)[0]
                rec["history"].append(rec["node"])
                rec["since"] = iso(now)
            rec["missing_since"] = None
            rec["image"] = info[name].get("image")
            rec["id"] = info[name].get("id")

        for name, rec in self.placement.items():
            if name not in present and rec.get("missing_since") is None:
                rec["missing_since"] = now

    # ---- output -------------------------------------------------------

    def _cluster(self) -> dict:
        nodes = list(self.nodes.values())
        up = [n for n in nodes if n.up]
        if all(n.up is None for n in nodes):
            status = "connecting"
        elif not up:
            status = "offline"
        elif self.lost or self.leader is None:
            status = "re-electing"
        elif len(up) < len(nodes):
            status = "degraded"
        elif self._votes.get(self.leader, 0) < len(up):
            status = "converging"
        else:
            status = "healthy"

        latencies = []
        for n in up:
            for nb in (n.snapshot or {}).get("neighbors", []):
                peer = self.nodes.get(nb["node_id"])
                if nb.get("latency_ms") is not None and peer is not None and peer.up:
                    latencies.append(nb["latency_ms"])

        return {
            "status": status,
            "leader": None if self.lost else self.leader,
            "previous_leader": self.leader if self.lost else None,
            "agree": 0 if self.lost else self._votes.get(self.leader, 0),
            "nodes_up": len(up),
            "nodes_total": len(nodes),
            "avg_latency_ms": round(sum(latencies) / len(latencies), 1) if latencies else None,
            "migrations": self.migrations,
        }

    def _node_view(self, n: _Node, now: datetime) -> dict:
        snap = n.snapshot or {}
        stats = snap.get("stats") or {}
        is_up = bool(n.up)
        neighbors = []
        if is_up:
            for nb in snap.get("neighbors", []):
                peer = self.nodes.get(nb["node_id"])
                peer_up = peer.up if peer else None
                neighbors.append(
                    {
                        "node_id": nb["node_id"],
                        "short": short(nb["node_id"]),
                        "up": peer_up,
                        "latency_ms": round(nb["latency_ms"], 1) if peer_up and nb.get("latency_ms") is not None else None,
                    }
                )
        return {
            "node_id": n.node_id,
            "short": short(n.node_id),
            "status": "connecting" if n.up is None else ("up" if n.up else "down"),
            "is_leader": is_up and n.node_id == self.leader and not self.lost,
            "region": snap.get("region"),
            "cluster": snap.get("cluster"),
            "uptime_seconds": snap.get("uptime_seconds") if is_up else None,
            "cpu": stats.get("cpu_percent") if is_up else None,
            "mem": stats.get("mem_percent") if is_up else None,
            "load_avg_5m": stats.get("history_load_avg_5m") if is_up else None,
            "trust": stats.get("trust_score") if is_up else None,
            "neighbors": neighbors,
            "containers": [c["name"] for c in (snap.get("containers") or [])] if is_up else [],
            "down_for": int((now - n.down_since).total_seconds()) if n.down_since else None,
        }

    def _container_views(self, now: datetime) -> list:
        views = []
        for rec in self.placement.values():
            node = self.nodes.get(rec["node"])
            missing = rec.get("missing_since")
            if missing is None:
                state = "running"
            elif node is not None and node.up is False:
                state = "node-down"
            elif (now - missing).total_seconds() <= 90:
                state = "in-transit"  # mid-migration: gone from the source, not yet running at the destination
            else:
                state = "stopped"
            views.append(
                {
                    "name": rec["name"],
                    "node": rec["node"],
                    "node_short": short(rec["node"]),
                    "state": state,
                    "history": [short(h) for h in rec["history"]],
                    "moves": len(rec["history"]) - 1,
                    "since": rec["since"],
                    "image": rec.get("image"),
                }
            )
        return sorted(views, key=lambda v: v["name"])

    def snapshot(self, now: datetime, event_limit: int = 200) -> dict:
        ordered = sorted(self.events, key=lambda e: (e["ts"], e["id"]), reverse=True)
        return {
            "generated_at": iso(now),
            "cluster": self._cluster(),
            "nodes": [self._node_view(n, now) for n in self.nodes.values()],
            "containers": self._container_views(now),
            "events": ordered[:event_limit],
        }
