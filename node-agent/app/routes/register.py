import logging

from fastapi import APIRouter, Depends

from app.config import settings
from app.deps import get_registry
from app.models import RegisterRequest, RegisterResponse
from app.neighbors import PeerRegistry

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/register", response_model=RegisterResponse)
async def register_peer(body: RegisterRequest, registry: PeerRegistry = Depends(get_registry)):
    if not registry.is_configured(body.node_id):
        logger.warning(
            "register from unconfigured node_id=%s (not in neighbors.yaml) — "
            "accepted anyway, no identity verification until mTLS is wired in",
            body.node_id,
        )
    registry.mark_registered(body.node_id, body.address, body.region, body.cluster)
    return RegisterResponse(status="registered", node_id=settings.NODE_ID)
