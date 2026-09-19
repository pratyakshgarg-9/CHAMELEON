from typing import Optional

from fastapi import HTTPException, Request

from app.clients import post_json
from app.config import settings
from app.election import ElectionState
from app.neighbors import PeerRegistry


def get_registry(request: Request) -> PeerRegistry:
    return request.app.state.registry


def get_election_state(request: Request) -> ElectionState:
    return request.app.state.election


def get_verified_cn(request: Request) -> Optional[str]:
    """The CN nginx verified on the mTLS handshake for this request,
    forwarded as X-SSL-Client-CN (see deploy/nginx.conf). None when the
    sidecar isn't in front of us (mTLS off, or local/dev/test where routes
    are hit directly) — callers decide whether that's acceptable via
    settings.MTLS_ENABLED, same gating the rest of the mTLS mechanism uses.
    Not itself a source of trust: this header only means something because
    nginx's ssl_verify_client already rejected anything without a
    CA-signed cert before this request could ever arrive.
    """
    return request.headers.get("X-SSL-Client-CN") or None


async def require_cn_matches(claimed_node_id: Optional[str], verified_cn: Optional[str]) -> None:
    """Enforce that a self-claimed identity in a request body matches the
    cert CN nginx verified for this connection. A no-op when MTLS_ENABLED
    is false, so local/dev/test traffic (no sidecar, no header) is
    unaffected — same gating every other mTLS-dependent code path uses.

    A mismatch (not a missing header — that's a misconfigured sidecar,
    not an identifiable actor) is also auto-reported to trust-service as
    an auth_failure against the cert's real identity (verified_cn, not
    the false claim) — that's what lets repeated impersonation attempts
    actually drive a node toward isolation instead of just being logged
    once and forgotten.
    """
    if not settings.MTLS_ENABLED or claimed_node_id is None:
        return
    if verified_cn is None:
        raise HTTPException(403, "mTLS enabled but no verified client CN present")
    if verified_cn != claimed_node_id:
        await post_json(f"{settings.TRUST_URL}/trust/report", {"node_id": verified_cn, "event_type": "auth_failure"})
        raise HTTPException(403, f"claimed node_id {claimed_node_id!r} does not match verified cert CN {verified_cn!r}")
