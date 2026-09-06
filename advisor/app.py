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

from fastapi import FastAPI
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
CA_CERT_PATH = os.environ.get("CA_CERT_PATH", "../shared/certs/ca.crt")
SERVER_CERT_PATH = os.environ.get("SERVER_CERT_PATH", "./certs/node.crt")
SERVER_KEY_PATH = os.environ.get("SERVER_KEY_PATH", "./certs/node.key")

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


if __name__ == "__main__":
    import ssl

    import uvicorn

    run_kwargs = {"host": "0.0.0.0", "port": 8100}

    if MTLS_ENABLED:
        run_kwargs.update(
            ssl_certfile=SERVER_CERT_PATH,
            ssl_keyfile=SERVER_KEY_PATH,
            ssl_ca_certs=CA_CERT_PATH,
            # CA-trust-only: any cert signed by our CA is accepted. No
            # per-request check that the cert's CN matches the caller's
            # claimed node_id — uvicorn's default ASGI transport doesn't
            # expose the peer certificate to route handlers without a
            # custom transport. Deferred (time constraint before review,
            # not an oversight) — see node-agent/node-agent-CLAUDE.md and
            # /shared/CONTRACT.md for the full note.
            # TODO(mTLS-CN-check): verify peer cert CN == caller's node_id here.
            ssl_cert_reqs=ssl.CERT_REQUIRED,
        )

    uvicorn.run(app, **run_kwargs)
