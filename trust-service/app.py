from datetime import datetime, timezone
from typing import Dict, List
import os
import sqlite3

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field


app = FastAPI(
    title="CHAMELEON Trust Service",
    description="Trust scoring service for CHAMELEON edge nodes",
    version="1.0.0",
)

# mTLS (server-side only — trust-service never calls another CHAMELEON
# service itself, per CONTRACT.md, so it only needs to require/verify a
# caller's cert, not present one). Off by default; see
# /shared/CONTRACT.md's mTLS section for the CA-trust-only scope this
# implements.
MTLS_ENABLED = os.environ.get("MTLS_ENABLED", "false").lower() == "true"
CA_CERT_PATH = os.environ.get("CA_CERT_PATH", "../shared/certs/ca.crt")
SERVER_CERT_PATH = os.environ.get("SERVER_CERT_PATH", "./certs/node.crt")
SERVER_KEY_PATH = os.environ.get("SERVER_KEY_PATH", "./certs/node.key")


# -------------------------------------------------------------------
# Models
# -------------------------------------------------------------------

class TrustScoreResponse(BaseModel):
    node_id: str
    trust_score: float = Field(ge=0.0, le=1.0)
    last_updated: str
    flags: List[str]
    
class TrustReport(BaseModel):
    node_id: str
    event_type: str


# -------------------------------------------------------------------
# In-memory trust state
# -------------------------------------------------------------------

trust_scores: Dict[str, TrustScoreResponse] = {}

# -------------------------------------------------------------------
# SQLite persistence
# -------------------------------------------------------------------

DB_PATH = "trust.db"


def init_db():
    conn = sqlite3.connect(DB_PATH)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS trust_state (
            node_id TEXT PRIMARY KEY,
            trust_score REAL NOT NULL,
            last_updated TEXT NOT NULL,
            flags TEXT NOT NULL
        )
    """)

    conn.commit()
    conn.close()


init_db()

def save_trust_state(current: TrustScoreResponse):
    conn = sqlite3.connect(DB_PATH)

    conn.execute(
        """
        INSERT OR REPLACE INTO trust_state
        (node_id, trust_score, last_updated, flags)
        VALUES (?, ?, ?, ?)
        """,
        (
            current.node_id,
            current.trust_score,
            current.last_updated,
            ",".join(current.flags)
        )
    )

    conn.commit()
    conn.close()
    
def load_trust_state(node_id: str):
    conn = sqlite3.connect(DB_PATH)

    row = conn.execute(
        """
        SELECT node_id, trust_score, last_updated, flags
        FROM trust_state
        WHERE node_id = ?
        """,
        (node_id,)
    ).fetchone()

    conn.close()

    if row is None:
        return None

    flags = [flag for flag in row[3].split(",") if flag]

    return TrustScoreResponse(
        node_id=row[0],
        trust_score=row[1],
        last_updated=row[2],
        flags=flags
    )
    
# -------------------------------------------------------------------
# Health check
# -------------------------------------------------------------------

@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "chameleon-trust-service"
    }


# -------------------------------------------------------------------
# Get trust score
# -------------------------------------------------------------------

@app.get("/trust/score/{node_id}", response_model=TrustScoreResponse)
def get_trust_score(node_id: str):

    # Return existing score if we have one
    if node_id in trust_scores:
        return trust_scores[node_id]

    # Initial score for a node that has not been evaluated yet.
    
        # Check SQLite for a previously saved state
    saved_state = load_trust_state(node_id)

    if saved_state is not None:
        trust_scores[node_id] = saved_state
        return saved_state
    
    # The exact project policy for initial trust is not specified
    # in the shared contract, so this is kept as a temporary default.
    result = TrustScoreResponse(
        node_id=node_id,
        trust_score=0.5,
        last_updated=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        flags=[]
    )

    trust_scores[node_id] = result

    return result

# -------------------------------------------------------------------
# Report a trust-related event
# -------------------------------------------------------------------

@app.post("/trust/report")
def report_trust_event(report: TrustReport):

    # Create the node's score if it does not exist yet
    if report.node_id not in trust_scores:
        trust_scores[report.node_id] = TrustScoreResponse(
            node_id=report.node_id,
            trust_score=0.5,
            last_updated=datetime.now(timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
            flags=[]
        )

    current = trust_scores[report.node_id]

        # Repeated authentication failure
    if report.event_type == "auth_failure":
        current.trust_score = max(0.0, current.trust_score - 0.2)

        if "auth_failure" not in current.flags:
            current.flags.append("auth_failure")

    # Inconsistent statistics
    elif report.event_type == "inconsistent_stats":
        current.trust_score = max(0.0, current.trust_score - 0.1)

        if "inconsistent_stats" not in current.flags:
            current.flags.append("inconsistent_stats")

    # Successful/clean migration
    elif report.event_type == "clean_migration":
        current.trust_score = min(1.0, current.trust_score + 0.05)

    # A temporary VM restart/dropout is not treated as malicious.
    elif report.event_type in ("vm_restart", "temporary_dropout"):
        pass

    # Update timestamp (was nested inside the elif above by an indentation
    # slip, so it only ever fired for vm_restart/temporary_dropout —
    # auth_failure/inconsistent_stats/clean_migration left last_updated
    # stale. Verified live with a TestClient before and after this fix.)
    current.last_updated = (
        datetime.now(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )

    save_trust_state(current)

    return current

# -------------------------------------------------------------------
# Isolate a node
# -------------------------------------------------------------------

@app.post("/trust/isolate/{node_id}")
def isolate_node(node_id: str):

    # Create the node if it does not exist yet
    if node_id not in trust_scores:
        trust_scores[node_id] = TrustScoreResponse(
            node_id=node_id,
            trust_score=0.5,
            last_updated=datetime.now(timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
            flags=[]
        )

    current = trust_scores[node_id]

    # Isolation immediately sets trust to zero
    current.trust_score = 0.0

    if "isolated" not in current.flags:
        current.flags.append("isolated")

    current.last_updated = (
        datetime.now(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )

    save_trust_state(current)

    return current


if __name__ == "__main__":
    import ssl

    import uvicorn

    run_kwargs = {"host": "0.0.0.0", "port": 8200}

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