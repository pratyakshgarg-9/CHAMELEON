import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException

from app.clients import get_json
from app.config import settings
from app.deps import get_election_state, get_registry, get_verified_cn, require_cn_matches
from app.election import ElectionState, start_election
from app.models import (
    CoordinatorRequest,
    CoordinatorResponse,
    ElectionRequest,
    ElectionResponse,
    LeaderResponse,
)
from app.neighbors import PeerRegistry

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/election", response_model=ElectionResponse)
async def election(
    body: ElectionRequest,
    registry: PeerRegistry = Depends(get_registry),
    state: ElectionState = Depends(get_election_state),
    verified_cn: str | None = Depends(get_verified_cn),
):
    await require_cn_matches(body.from_node_id, verified_cn)
    # Bully protocol: reply immediately (this node is alive and higher-ID,
    # so the caller backs off), then run our own election independently —
    # fire-and-forget, not awaited, so the response isn't held up by it.
    asyncio.create_task(start_election(registry, state))
    return ElectionResponse(status="ok", node_id=settings.NODE_ID)


@router.post("/coordinator", response_model=CoordinatorResponse)
async def coordinator(
    body: CoordinatorRequest,
    registry: PeerRegistry = Depends(get_registry),
    state: ElectionState = Depends(get_election_state),
    verified_cn: str | None = Depends(get_verified_cn),
):
    await require_cn_matches(body.leader_node_id, verified_cn)
    # election.py's _cluster_peers keeps OTHER nodes from deferring to or
    # announcing an isolated peer during their OWN election — but that
    # doesn't stop an isolated node from unilaterally self-declaring and
    # announcing itself here, which everyone previously just accepted
    # unconditionally. Same trust-service check, applied at the one
    # remaining place a would-be leader's status actually gets enforced.
    trust = await get_json(f"{settings.TRUST_URL}/trust/score/{body.leader_node_id}")
    if trust is not None and (trust.get("trust_score", 1.0) <= 0.0 or "isolated" in (trust.get("flags") or [])):
        logger.warning("rejecting coordinator announcement from isolated node %s", body.leader_node_id)
        raise HTTPException(403, f"{body.leader_node_id!r} is isolated and cannot be leader")
    state.current_leader = body.leader_node_id
    # A coordinator announcement is itself fresh evidence the leader is
    # alive — without this, the staleness check in check_leader_liveness
    # (based on the heartbeat loop's last_seen, unrelated to this message)
    # can fire a spurious "hasn't been seen" warning moments after this
    # exact announcement confirmed otherwise.
    registry.mark_seen(body.leader_node_id)
    logger.info("acknowledged new leader: %s", body.leader_node_id)
    return CoordinatorResponse(status="ack", node_id=settings.NODE_ID)


@router.get("/leader", response_model=LeaderResponse)
async def leader(state: ElectionState = Depends(get_election_state)):
    return LeaderResponse(leader_node_id=state.current_leader, node_id=settings.NODE_ID)
