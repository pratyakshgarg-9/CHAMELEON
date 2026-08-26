from fastapi import APIRouter

from app.config import settings

router = APIRouter()


@router.get("/health")
async def health():
    return {"status": "ok", "node_id": settings.NODE_ID}
