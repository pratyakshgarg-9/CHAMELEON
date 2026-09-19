from datetime import datetime, timezone
from typing import Any, Dict
import os

from fastapi import FastAPI, HTTPException, Request


app = FastAPI(
    title="CHAMELEON Global Coordinator",
    description="Global coordinator skeleton for CHAMELEON",
    version="1.0.0",
)

# mTLS (server-side only — the coordinator never calls another CHAMELEON
# service itself, per CONTRACT.md, so it only needs to require/verify a
# caller's cert, not present one). Off by default; see
# /shared/CONTRACT.md's mTLS section for the CA-trust-only scope this
# implements.
MTLS_ENABLED = os.environ.get("MTLS_ENABLED", "false").lower() == "true"
BIND_HOST = os.environ.get("HOST", "0.0.0.0")
BIND_PORT = int(os.environ.get("PORT", "9000"))


def require_cn_matches(claimed_node_id: Any, request: Request) -> None:
    """See advisor/app.py's twin of this function for the full rationale.
    claimed_node_id is None when the payload dict simply didn't include
    that key — left unenforced here (a missing/malformed field is a
    validation problem, not an identity mismatch); it'll surface as a
    KeyError/None downstream instead of a misleading 403.
    """
    if not MTLS_ENABLED or claimed_node_id is None:
        return
    verified_cn = request.headers.get("X-SSL-Client-CN") or None
    if verified_cn is None:
        raise HTTPException(403, "mTLS enabled but no verified client CN present")
    if verified_cn != claimed_node_id:
        raise HTTPException(403, f"claimed node_id {claimed_node_id!r} does not match verified cert CN {verified_cn!r}")


# -------------------------------------------------------------------
# Health check
# -------------------------------------------------------------------

@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "chameleon-global-coordinator"
    }


# -------------------------------------------------------------------
# Register a regional leader
# -------------------------------------------------------------------

@app.post("/coordinator/register")
def register_region(payload: Dict[str, Any], request: Request):
    require_cn_matches(payload.get("leader_node_id"), request)
    print(
        f"[COORDINATOR] Region registration received: "
        f"{payload}"
    )

    return {
        "status": "accepted",
        "message": "Regional registration received",
        "received_at": datetime.now(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    }


# -------------------------------------------------------------------
# Escalate an issue to the global coordinator
# -------------------------------------------------------------------

@app.post("/coordinator/escalate")
def escalate(payload: Dict[str, Any], request: Request):
    require_cn_matches(payload.get("node_id"), request)
    print(
        f"[COORDINATOR] Escalation received: "
        f"{payload}"
    )

    return {
        "status": "accepted",
        "message": "Escalation received",
        "received_at": datetime.now(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    }


if __name__ == "__main__":
    import uvicorn

    # Plain HTTP here even when MTLS_ENABLED — the nginx sidecar in front
    # of this process terminates the mTLS handshake (deploy/nginx.conf)
    # and proxies to us over plain HTTP; see advisor/app.py's twin comment.
    run_kwargs = {"host": BIND_HOST, "port": BIND_PORT}

    uvicorn.run(app, **run_kwargs)