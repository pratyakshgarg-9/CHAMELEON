"""
app.py — FastAPI service for the CHAMELEON AI Advisor.

Endpoints:
  GET  /health     -> liveness check
  POST /recommend   -> score candidates, return the recommended migration target

Run with:
  uvicorn app:app --host 0.0.0.0 --port 8100
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from models import CandidateScore, HealthResponse, RecommendRequest, RecommendResponse
from scorer import score_candidates

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("chameleon.advisor")

app = FastAPI(
    title="CHAMELEON AI Advisor",
    description="Scores candidate nodes and recommends a migration target for overloaded nodes.",
    version="0.1.0",
)


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse()


@app.post("/recommend", response_model=RecommendResponse)
def recommend(request: RecommendRequest) -> RecommendResponse:

    result = score_candidates(
        candidates=request.candidates,
        overloaded_node=request.overloaded_node,
    )

    if result.winner is None:
        logger.warning(
            "No valid candidate for overloaded_node=%s",
            request.overloaded_node,
        )

        return RecommendResponse(
            recommended_node="none",
            score=0.0,
            reasoning=result.reasoning,
        )

    logger.info(
        "overloaded_node=%s -> recommended=%s score=%.3f",
        request.overloaded_node,
        result.winner.node_id,
        result.winner.score,
    )

    return RecommendResponse(
        recommended_node=result.winner.node_id,
        score=result.winner.score,
        reasoning=result.reasoning,
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request, exc):
    # Defensive catch-all so a scoring bug returns a clean 500 instead of
    # crashing the worker; scheduler integrations should treat any non-2xx
    # response as "advisor unavailable, fall back to your own logic".
    logger.exception("Unhandled error processing request")
    return JSONResponse(status_code=500, content={"error": "internal_error", "detail": str(exc)})
