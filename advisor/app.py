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
import os

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from models import CandidateScore, HealthResponse, RecommendRequest, RecommendResponse
from scorer import score_candidates

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("chameleon.advisor")

# mTLS (server-side only — advisor never calls another CHAMELEON service
# itself, per CONTRACT.md, so it only needs to require/verify a caller's
# cert, not present one). Off by default; see /shared/CONTRACT.md's mTLS
# section for the CA-trust-only scope this implements.
MTLS_ENABLED = os.environ.get("MTLS_ENABLED", "false").lower() == "true"
BIND_HOST = os.environ.get("HOST", "0.0.0.0")
BIND_PORT = int(os.environ.get("PORT", "8100"))


def require_cn_matches(claimed_node_id: str, request: Request) -> None:
    """When MTLS_ENABLED, the nginx sidecar in front of this service (see
    deploy/nginx.conf) has already terminated the mTLS handshake and
    forwarded the verified client cert's CN as X-SSL-Client-CN. This
    checks that CN against the node_id the caller is claiming (here:
    overloaded_node — the calling node-agent reporting on itself, per
    CONTRACT.md's /recommend shape). CA-trust-only mTLS (what existed
    before this check) verifies the caller has *a* cert we trust; this
    closes the gap of verifying it's *that node's* cert.
    """
    if not MTLS_ENABLED:
        return
    verified_cn = request.headers.get("X-SSL-Client-CN") or None
    if verified_cn is None:
        raise HTTPException(403, "mTLS enabled but no verified client CN present")
    if verified_cn != claimed_node_id:
        raise HTTPException(403, f"claimed node_id {claimed_node_id!r} does not match verified cert CN {verified_cn!r}")

app = FastAPI(
    title="CHAMELEON AI Advisor",
    description="Scores candidate nodes and recommends a migration target for overloaded nodes.",
    version="0.1.0",
)


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse()


@app.post("/recommend", response_model=RecommendResponse)
def recommend(request: RecommendRequest, http_request: Request) -> RecommendResponse:
    require_cn_matches(request.overloaded_node, http_request)

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


if __name__ == "__main__":
    import uvicorn

    # host/port are plain HTTP even when MTLS_ENABLED — the mTLS handshake
    # itself now happens in the nginx sidecar in front of this process
    # (see deploy/nginx.conf), not in uvicorn directly. BIND_HOST/BIND_PORT
    # default to 0.0.0.0:8100 (today's behavior) for the no-sidecar path;
    # set HOST=127.0.0.1/PORT=8101 in the environment when nginx fronts
    # this service, so only the sidecar can reach it.
    run_kwargs = {"host": BIND_HOST, "port": BIND_PORT}

    uvicorn.run(app, **run_kwargs)
