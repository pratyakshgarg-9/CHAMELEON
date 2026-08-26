from fastapi import APIRouter, Depends

from app.deps import get_registry
from app.models import StatsPayload
from app.neighbors import PeerRegistry
from app.stats import build_stats_payload

router = APIRouter()


@router.get("/stats", response_model=StatsPayload)
async def get_stats(registry: PeerRegistry = Depends(get_registry)):
    return await build_stats_payload(registry)
