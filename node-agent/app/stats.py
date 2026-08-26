import asyncio
import logging
from collections import deque

import psutil
from datetime import datetime, timezone

from app.clients import get_json
from app.config import settings
from app.models import StatsPayload
from app.neighbors import PeerRegistry

logger = logging.getLogger(__name__)

# ~5 minutes of history at a 10s sample interval
_SAMPLE_INTERVAL_SECONDS = 10
_SAMPLE_WINDOW = 30

_cpu_samples: deque = deque(maxlen=_SAMPLE_WINDOW)


async def run_cpu_sampler() -> None:
    """Background task: periodically samples CPU utilization so /stats can
    report a real rolling load average instead of one instantaneous reading.
    """
    while True:
        _cpu_samples.append(psutil.cpu_percent(interval=None))
        await asyncio.sleep(_SAMPLE_INTERVAL_SECONDS)


def _history_load_avg_5m() -> float:
    if not _cpu_samples:
        return psutil.cpu_percent(interval=None)
    return sum(_cpu_samples) / len(_cpu_samples)


async def _fetch_trust_score() -> float:
    url = f"{settings.TRUST_URL}/trust/score/{settings.NODE_ID}"
    body = await get_json(url)
    if body is None:
        logger.warning("trust-service unreachable, defaulting trust_score=1.0 for %s", settings.NODE_ID)
        return 1.0
    return float(body.get("trust_score", 1.0))


async def build_stats_payload(registry: PeerRegistry) -> StatsPayload:
    trust_score = await _fetch_trust_score()

    # Populated by the outbound heartbeat loop (app/heartbeat_loop.py) as it
    # measures round-trip time to each neighbor. A peer that hasn't answered
    # yet (or ever) is simply omitted rather than reported as 0.
    latency_ms = {
        p.node_id: p.latency_ms for p in registry.list_all() if p.latency_ms is not None
    }

    return StatsPayload(
        node_id=settings.NODE_ID,
        timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        cpu_percent=psutil.cpu_percent(interval=None),
        mem_percent=psutil.virtual_memory().percent,
        latency_ms=latency_ms,
        history_load_avg_5m=_history_load_avg_5m(),
        trust_score=trust_score,
    )
