import asyncio
import logging
import time

from app.clients import post_json
from app.config import settings
from app.neighbors import PeerRegistry, PeerState

logger = logging.getLogger(__name__)


async def _ping_peer(registry: PeerRegistry, peer: PeerState) -> None:
    start = time.perf_counter()
    result = await post_json(f"{peer.url}/heartbeat", {"from_node_id": settings.NODE_ID})
    elapsed_ms = (time.perf_counter() - start) * 1000

    if result is None:
        # A transient miss isn't treated as a hard failure — see
        # /shared/CONTRACT.md's "failure vs. malice" note. Trust Service
        # decides what a pattern of misses means, not us; we just stop
        # reporting a fresh latency for this peer until it answers again.
        logger.warning("heartbeat to %s (%s) failed", peer.node_id, peer.url)
        return

    registry.update_latency(peer.node_id, elapsed_ms)


async def heartbeat_once(registry: PeerRegistry) -> None:
    """One pass: pings every known peer concurrently so a single slow/down
    neighbor can't stall latency measurement of the others."""
    peers = registry.list_all()
    if not peers:
        return
    await asyncio.gather(*(_ping_peer(registry, peer) for peer in peers), return_exceptions=True)


async def run_heartbeat_loop(registry: PeerRegistry) -> None:
    """Background task: the outbound side of component 3 — periodically
    pings every neighbor's inbound /heartbeat and logs round-trip latency.
    """
    while True:
        await heartbeat_once(registry)
        await asyncio.sleep(settings.HEARTBEAT_INTERVAL_SECONDS)
