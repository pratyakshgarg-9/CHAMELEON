import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from app.clients import post_json
from app.config import settings
from app.neighbors import PeerRegistry

logger = logging.getLogger(__name__)


class ElectionState:
    def __init__(self):
        self.current_leader: Optional[str] = None
        self.election_in_progress: bool = False


def _cluster_peers(registry: PeerRegistry) -> list:
    """Peers in this node's own region+cluster — Bully election is scoped
    'within a regional cluster' per node-agent-CLAUDE.md. Only peers that
    have actually /register'ed are considered (that's how we learn their
    region/cluster) — a statically-configured-but-never-seen neighbor can't
    be reliably placed in a cluster.
    """
    return [p for p in registry.list_all() if p.region == settings.REGION and p.cluster == settings.CLUSTER]


async def start_election(registry: PeerRegistry, state: ElectionState) -> None:
    """The Bully core: ping every higher-node_id cluster peer. If any is
    alive, defer — it will run its own election and, if it wins, announce
    itself. If none respond (including the trivial case of no higher peers
    at all), this node wins and announces itself as leader.
    """
    if state.election_in_progress:
        return
    state.election_in_progress = True
    try:
        higher = [p for p in _cluster_peers(registry) if p.node_id > settings.NODE_ID]
        if higher:
            responses = await asyncio.gather(
                *(post_json(f"{p.url}/election", {"from_node_id": settings.NODE_ID}) for p in higher)
            )
            if any(r is not None for r in responses):
                logger.info(
                    "deferring election — a higher-ID node in %s/%s is alive", settings.REGION, settings.CLUSTER
                )
                return
        await _become_leader(registry, state)
    finally:
        state.election_in_progress = False


async def _become_leader(registry: PeerRegistry, state: ElectionState) -> None:
    if state.current_leader != settings.NODE_ID:
        logger.warning("this node is now the leader of %s/%s", settings.REGION, settings.CLUSTER)
    state.current_leader = settings.NODE_ID

    # Always (re-)announce to every currently-known cluster peer, even if
    # this node already believed itself leader — not just on the
    # transition. A peer that self-elected before it knew about this node
    # (the startup race described below), or one that joined late, or one
    # that missed the original announcement, all get corrected by the next
    # tick's broadcast instead of staying wrong forever.
    peers = _cluster_peers(registry)
    if peers:
        await asyncio.gather(
            *(post_json(f"{p.url}/coordinator", {"leader_node_id": settings.NODE_ID}) for p in peers),
            return_exceptions=True,
        )


async def check_leader_liveness(registry: PeerRegistry, state: ElectionState) -> None:
    """Runs every tick — including when this node already believes itself
    the leader. That's deliberate: a node can trivially "win" its own
    bootstrap election before it has finished learning about peers (that
    registration happens on a separate concurrent task), and if a
    self-declared leader never re-checked, that race would leave a
    permanent split-brain with multiple nodes each wrongly convinced
    they're the leader — exactly what a live multi-node run surfaced.
    Re-running the check unconditionally makes it self-healing: a startup
    race, a network partition, or a late-joining higher-ID peer all resolve
    within a tick or two. The cost is a handful of small JSON pings every
    ELECTION_CHECK_INTERVAL_SECONDS among a few nodes — negligible at this
    project's scale.
    """
    if state.current_leader is None:
        logger.info("no known leader in %s/%s yet", settings.REGION, settings.CLUSTER)
    elif state.current_leader != settings.NODE_ID:
        peer = next((p for p in registry.list_all() if p.node_id == state.current_leader), None)
        stale_after = settings.HEARTBEAT_INTERVAL_SECONDS * settings.MISSED_HEARTBEATS_BEFORE_ELECTION
        if peer is None or peer.last_seen is None or _seconds_since(peer.last_seen) > stale_after:
            logger.warning(
                "leader %s hasn't been seen in over %ss — starting an election", state.current_leader, stale_after
            )

    await start_election(registry, state)


def _seconds_since(when: datetime) -> float:
    return (datetime.now(timezone.utc) - when).total_seconds()


async def run_election_monitor(registry: PeerRegistry, state: ElectionState) -> None:
    while True:
        try:
            await check_leader_liveness(registry, state)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("election monitor tick failed — will retry next cycle")
        await asyncio.sleep(settings.ELECTION_CHECK_INTERVAL_SECONDS)
