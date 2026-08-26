import asyncio
import logging

import psutil
from pydantic import ValidationError

from app.clients import get_json, post_json
from app.config import settings
from app.migration import migrate_container
from app.models import RecommendResponse, StatsPayload
from app.neighbors import PeerRegistry

logger = logging.getLogger(__name__)


class OverloadTracker:
    """Tracks consecutive overloaded readings so a single brief spike
    doesn't trigger a migration — only SUSTAINED_POLLS in a row does.
    """

    def __init__(self):
        self._consecutive = 0

    def record(self, overloaded: bool) -> bool:
        """Returns True exactly once sustained overload is reached, then
        resets so it doesn't refire on the very next tick."""
        if not overloaded:
            self._consecutive = 0
            return False

        self._consecutive += 1
        if self._consecutive >= settings.SUSTAINED_POLLS:
            self._consecutive = 0
            return True
        return False


async def _gather_candidate_stats(registry: PeerRegistry) -> list[StatsPayload]:
    """Fetches every known neighbor's own live /stats — candidates are
    migration *destinations*, so this node's own stats are never included.
    Unreachable or malformed responses are silently excluded (same
    fail-safe convention as everywhere else — get_json already returns
    None on failure)."""
    peers = registry.list_all()
    bodies = await asyncio.gather(*(get_json(f"{p.url}/stats") for p in peers))

    candidates = []
    for peer, body in zip(peers, bodies):
        if body is None:
            continue
        try:
            candidates.append(StatsPayload(**body))
        except ValidationError:
            logger.warning("neighbor %s returned a malformed stats payload — excluding it", peer.node_id)
    return candidates


async def _attempt_migration(registry: PeerRegistry) -> None:
    candidates = await _gather_candidate_stats(registry)
    if not candidates:
        logger.warning("no reachable neighbors to consider for migration — skipping this cycle")
        return

    request_body = {
        "overloaded_node": settings.NODE_ID,
        "candidates": [c.model_dump() for c in candidates],
    }
    result = await post_json(f"{settings.ADVISOR_URL}/recommend", request_body)
    if result is None:
        logger.warning("advisor unreachable or timed out — skipping migration this cycle")
        return

    try:
        recommendation = RecommendResponse(**result)
    except ValidationError as exc:
        logger.warning("advisor returned a malformed response (%s) — skipping migration this cycle", exc)
        return

    if recommendation.recommended_node == "none":
        logger.info("advisor recommended no migration this cycle")
        return

    candidate_ids = {c.node_id for c in candidates}
    if recommendation.recommended_node not in candidate_ids:
        logger.warning(
            "advisor recommended %r, which wasn't one of the candidates sent — ignoring",
            recommendation.recommended_node,
        )
        return

    logger.warning(
        "triggering migration of %s to %s (advisor score=%.2f: %s)",
        settings.MANAGED_CONTAINER_NAME,
        recommendation.recommended_node,
        recommendation.score,
        recommendation.reasoning,
    )
    outcome = await migrate_container(registry, settings.MANAGED_CONTAINER_NAME, recommendation.recommended_node)
    logger.warning("migration outcome: %s", outcome.model_dump())


async def scheduler_tick(registry: PeerRegistry, tracker: OverloadTracker) -> None:
    if not settings.MANAGED_CONTAINER_NAME:
        return

    cpu = psutil.cpu_percent(interval=None)
    mem = psutil.virtual_memory().percent
    overloaded_now = cpu >= settings.CPU_OVERLOAD_THRESHOLD or mem >= settings.MEM_OVERLOAD_THRESHOLD

    if not tracker.record(overloaded_now):
        return

    logger.warning(
        "sustained overload detected (cpu=%.1f%% mem=%.1f%%) — asking advisor for a migration target",
        cpu,
        mem,
    )
    await _attempt_migration(registry)


async def run_scheduler_loop(registry: PeerRegistry, tracker: OverloadTracker) -> None:
    while True:
        try:
            await scheduler_tick(registry, tracker)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Never let one bad cycle (a Docker error, an unexpected
            # exception anywhere in the migration path, ...) permanently
            # kill the scheduler — every other background loop in this
            # codebase is built to fail safe and keep going; this one was
            # not, and a real run surfaced exactly that gap.
            logger.exception("scheduler tick failed — will retry next cycle")
        await asyncio.sleep(settings.SCHEDULER_INTERVAL_SECONDS)
