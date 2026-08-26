from fastapi import APIRouter, Depends

from app.deps import get_registry
from app.neighbors import PeerRegistry

router = APIRouter()


@router.get("/neighbors")
async def get_neighbors(registry: PeerRegistry = Depends(get_registry)):
    peers = registry.list_all()
    return {
        "neighbors": [
            {
                "node_id": p.node_id,
                "url": p.url,
                "region": p.region,
                "cluster": p.cluster,
                "registered": p.registered,
                "last_seen": p.last_seen.strftime("%Y-%m-%dT%H:%M:%SZ") if p.last_seen else None,
            }
            for p in peers
        ]
    }
