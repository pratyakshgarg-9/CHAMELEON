from datetime import datetime, timezone
from typing import Any, Dict
import os

from fastapi import FastAPI


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
CA_CERT_PATH = os.environ.get("CA_CERT_PATH", "../shared/certs/ca.crt")
SERVER_CERT_PATH = os.environ.get("SERVER_CERT_PATH", "./certs/node.crt")
SERVER_KEY_PATH = os.environ.get("SERVER_KEY_PATH", "./certs/node.key")


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
def register_region(payload: Dict[str, Any]):
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
def escalate(payload: Dict[str, Any]):
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
    import ssl

    import uvicorn

    run_kwargs = {"host": "0.0.0.0", "port": 9000}

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