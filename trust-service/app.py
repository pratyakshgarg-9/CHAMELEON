from datetime import datetime, timezone
from typing import Dict, List
import os
import sqlite3

from fastapi import FastAPI, HTTPException, Request
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
BIND_HOST = os.environ.get("HOST", "0.0.0.0")
BIND_PORT = int(os.environ.get("PORT", "8200"))


def require_cn_matches(claimed_node_id: str, request: Request) -> None:
    """See advisor/app.py's twin of this function for the full rationale.
    When MTLS_ENABLED, the nginx sidecar (deploy/nginx.conf) has already
    terminated the mTLS handshake and forwards the verified client cert's
    CN as X-SSL-Client-CN; this checks it against the node_id a caller
    claims to be reporting about itself.
    """
    if not MTLS_ENABLED:
        return
    verified_cn = request.headers.get("X-SSL-Client-CN") or None
    if verified_cn is None:
        raise HTTPException(403, "mTLS enabled but no verified client CN present")
    if verified_cn != claimed_node_id:
        raise HTTPException(403, f"claimed node_id {claimed_node_id!r} does not match verified cert CN {verified_cn!r}")


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
def get_trust_score(node_id: str, request: Request):
    # node-agent only ever queries its own trust_score (stats.py:38 always
    # passes settings.NODE_ID) — a self-lookup, so CN-checking it is safe.
    # /trust/report below is deliberately NOT CN-checked: its node_id
    # could plausibly be a caller reporting on itself OR flagging another
    # node's misbehavior, and nothing in the current codebase calls it yet
    # to settle which — enforcing self-match here would silently break
    # third-party reporting if that's the intended use.
    require_cn_matches(node_id, request)

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
    import uvicorn

    # Plain HTTP here even when MTLS_ENABLED — the nginx sidecar in front
    # of this process terminates the mTLS handshake (deploy/nginx.conf)
    # and proxies to us over plain HTTP; see advisor/app.py's twin comment.
    run_kwargs = {"host": BIND_HOST, "port": BIND_PORT}

    uvicorn.run(app, **run_kwargs)