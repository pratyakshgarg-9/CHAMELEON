import asyncio
import logging

from fastapi import APIRouter, Depends

from app.config import settings
from app.deps import get_election_state, get_registry
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
):
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
):
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
