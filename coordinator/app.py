from datetime import datetime, timezone
from typing import Any, Dict

from fastapi import FastAPI


app = FastAPI(
    title="CHAMELEON Global Coordinator",
    description="Global coordinator skeleton for CHAMELEON",
    version="1.0.0",
)


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