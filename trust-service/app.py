from datetime import datetime, timezone
from typing import Dict, List
import os
import sqlite3

from fastapi import FastAPI
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


# -------------------------------------------------------------------
# Models
# -------------------------------------------------------------------

class TrustScoreResponse(BaseModel):
    node_id: str
    trust_score: float = Field(ge=0.0, le=1.0)
    last_updated: str
    flags: List[str]
    
class TrustReport(BaseModel):
    # Deliberately NOT CN-checked (unlike /register): this is inherently
    # a third-party report, not a self-claim — node-agent's actual caller
    # (app/deps.py:require_cn_matches) is the node that DETECTED bad
    # behavior, reporting about the node_id that exhibited it, which are
    # two different identities by construction. A same-identity check
    # here would reject every real report node-agent ever sends.
    # Accepted limitation: any mTLS-authenticated (CA-signed cert) caller
    # can report any node_id, with no corroboration from other members —
    # a single compromised or bugged node could falsely tank a healthy
    # peer's score. Not addressed here; would need multi-reporter
    # corroboration or a provenance/audit trail to close, out of scope
    # for this project's timeline.
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
    # Deliberately NOT CN-checked, unlike /register or /trust/report: this
    # is a read-only query ABOUT node_id, not a claim of BEING node_id —
    # any mTLS-authenticated caller in the mesh needs to check any OTHER
    # node's trust score to make election/migration decisions about it
    # (node-agent's election.py does exactly this). CN-checking it would
    # only ever allow self-queries, which defeats the entire point of a
    # trust score other nodes are supposed to consult.

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

    # Auto-isolate the moment a score bottoms out — otherwise "isolates
    # suspicious nodes" would require some separate caller to notice and
    # hit /trust/isolate by hand, which nothing in the system does.
    # Checked after every event type (not just auth_failure), so any path
    # that drives the score to 0 isolates uniformly.
    if current.trust_score <= 0.0 and "isolated" not in current.flags:
        current.flags.append("isolated")

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