"""Local stand-in for Member 2's AI Advisor service.

Run on :8100 until the real service exists (root-CLAUDE.md status board).
Matches /shared/schemas/recommend_request.schema.json and
recommend_response.schema.json exactly, so swapping ADVISOR_URL to the real
service later is a config change, not a rewrite.

    uvicorn stubs.advisor_stub:app --port 8100
"""

from fastapi import FastAPI

from app.models import RecommendRequest, RecommendResponse

app = FastAPI(title="Advisor Stub")


@app.post("/recommend", response_model=RecommendResponse)
async def recommend(body: RecommendRequest) -> RecommendResponse:
    nearest = body.candidates[0].node_id
    return RecommendResponse(recommended_node=nearest, score=1.0, reasoning="stub: first candidate")
