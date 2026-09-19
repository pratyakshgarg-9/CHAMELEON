from datetime import datetime, timezone

from fastapi import APIRouter, Depends

from app.config import settings
from app.deps import get_registry, get_verified_cn, require_cn_matches
from app.models import HeartbeatRequest, HeartbeatResponse
from app.neighbors import PeerRegistry

router = APIRouter()


@router.post("/heartbeat", response_model=HeartbeatResponse)
async def heartbeat(
    body: HeartbeatRequest,
    registry: PeerRegistry = Depends(get_registry),
    verified_cn: str | None = Depends(get_verified_cn),
):
    await require_cn_matches(body.from_node_id, verified_cn)
    if body.from_node_id:
        registry.mark_seen(body.from_node_id)
    return HeartbeatResponse(
        node_id=settings.NODE_ID,
        timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        status="alive",
    )
