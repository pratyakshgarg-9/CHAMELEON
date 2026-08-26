"""Local stand-in for Member 3's Trust Service.

Run on :8200 until the real service exists (root-CLAUDE.md status board).
Matches /shared/schemas/trust_score_response.schema.json exactly, so
swapping TRUST_URL to the real service later is a config change, not a
rewrite.

    uvicorn stubs.trust_stub:app --port 8200
"""

from datetime import datetime, timezone

from fastapi import FastAPI

from app.models import TrustScoreResponse

app = FastAPI(title="Trust Service Stub")


@app.get("/trust/score/{node_id}", response_model=TrustScoreResponse)
async def trust_score(node_id: str) -> TrustScoreResponse:
    return TrustScoreResponse(
        node_id=node_id,
        trust_score=1.0,
        last_updated=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        flags=[],
    )
