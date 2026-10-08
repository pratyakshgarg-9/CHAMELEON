import asyncio
import logging
import time

from fastapi import APIRouter, Depends

from app import docker_client
from app.config import settings
from app.deps import get_election_state, get_registry
from app.election import ElectionState
from app.events import BOOT_ID, event_log
from app.neighbors import PeerRegistry
from app.stats import build_stats_payload

logger = logging.getLogger(__name__)
router = APIRouter()

_STARTED_AT = time.time()


@router.get("/status")
async def get_status(
    since: int = 0,
    registry: PeerRegistry = Depends(get_registry),
    state: ElectionState = Depends(get_election_state),
):
    """Everything the dashboard shows about this node in one round trip —
    one request per node per poll means "didn't answer" is an unambiguous
    signal that the node is down, instead of five calls that can each
    half-fail. `since` is the caller's event cursor (see app/events.py).
    """
    stats = await build_stats_payload(registry, cached_trust=True)

    containers, containers_error = [], None
    try:
        containers = await asyncio.to_thread(docker_client.list_app_containers)
    except Exception as exc:  # docker daemon down/unreachable shouldn't take the whole status down
        logger.warning("could not list containers for /status: %s", exc)
        containers_error = str(exc)

    return {
        "node_id": settings.NODE_ID,
        "region": settings.REGION,
        "cluster": settings.CLUSTER,
        "boot_id": BOOT_ID,
        "uptime_seconds": int(time.time() - _STARTED_AT),
        "leader_node_id": state.current_leader,
        "stats": stats.model_dump(),
        "neighbors": [
            {
                "node_id": p.node_id,
                "registered": p.registered,
                "last_seen": p.last_seen.strftime("%Y-%m-%dT%H:%M:%SZ") if p.last_seen else None,
                "latency_ms": p.latency_ms,
            }
            for p in registry.list_all()
        ],
        "containers": containers,
        "containers_error": containers_error,
        "managed_container": settings.MANAGED_CONTAINER_NAME or None,
        "events": event_log.since(since),
        "latest_seq": event_log.latest_seq,
    }
