import logging
import ssl
from typing import Optional, Union

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# Shared inter-service convention from /shared/CONTRACT.md: 3s connect / 5s
# read timeout, 2 retries, then fail safe (skip the action, don't crash).
TIMEOUT = httpx.Timeout(
    connect=3.0,
    read=5.0,
    write=5.0,
    pool=5.0,
)
MAX_ATTEMPTS = 3  # initial attempt + 2 retries

# Transferring a container image + having the receiver load/run/confirm-
# healthy it is real work, not a lightweight JSON call — the 5s convention
# above is too tight for it and risks a false-negative timeout on a request
# that actually succeeds server-side. post_multipart defaults to this
# instead; callers can still override.
MIGRATE_TIMEOUT = httpx.Timeout(
    connect=3.0,
    read=60.0,
    write=30.0,
    pool=60.0,
)

# mTLS(status): client-side is wired below, gated on settings.MTLS_ENABLED —
# see app/config.py. Two things still block turning it on for real: Member 3
# hasn't issued real per-node certs yet (see /shared/certs/README_2.md and
# root-CLAUDE.md's status checklist; scripts/generate_dev_certs.py produces
# throwaway dev certs to test against meanwhile), and there is no app-level
# check that a cert's CN matches the sender's claimed node_id — uvicorn's
# default ASGI transport doesn't expose the peer certificate to route
# handlers, so enforcement here is CA-trust-only (any cert signed by our CA
# is accepted), not per-identity. See the mTLS plan notes for why that's a
# deliberate scope call, not an oversight.
#
# NOTE: Member 3's branch (member3-security-trust) independently made mTLS
# unconditional here — cert=(...)/verify=... passed on every call with no
# flag. Verified that breaks node-agent outright: no CA cert exists
# anywhere in the repo yet, so httpx.AsyncClient(...) raises FileNotFoundError
# at construction, unhandled, killing the heartbeat/announce/scheduler
# background tasks on their first tick. Keeping the flag-gated version below
# instead — see the integration plan for the full reasoning.

_ssl_context: Union[ssl.SSLContext, bool, None] = None


def _get_ssl_context() -> Union[ssl.SSLContext, bool]:
    """Built once and cached. `verify=True` (default) when mTLS is off."""
    global _ssl_context
    if _ssl_context is None:
        if settings.MTLS_ENABLED:
            ctx = ssl.create_default_context(cafile=settings.CA_CERT_PATH)
            ctx.load_cert_chain(certfile=settings.CLIENT_CERT_PATH, keyfile=settings.CLIENT_KEY_PATH)
            _ssl_context = ctx
        else:
            _ssl_context = True
    return _ssl_context


async def post_json(url: str, json_body: dict) -> Optional[dict]:
    async with httpx.AsyncClient(timeout=TIMEOUT, verify=_get_ssl_context()) as client:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                resp = await client.post(url, json=json_body)
                resp.raise_for_status()
                return resp.json()
            except (httpx.HTTPError, ValueError) as exc:
                logger.warning(
                    "POST %s failed (attempt %d/%d): %s",
                    url,
                    attempt,
                    MAX_ATTEMPTS,
                    exc,
                )

    return None


async def post_multipart(
    url: str,
    files: dict,
    data: dict,
    timeout: httpx.Timeout = MIGRATE_TIMEOUT,
) -> Optional[dict]:
    async with httpx.AsyncClient(timeout=timeout, verify=_get_ssl_context()) as client:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                resp = await client.post(
                    url,
                    files=files,
                    data=data,
                )
                resp.raise_for_status()
                return resp.json()
            except (httpx.HTTPError, ValueError) as exc:
                logger.warning(
                    "POST %s (multipart) failed (attempt %d/%d): %s",
                    url,
                    attempt,
                    MAX_ATTEMPTS,
                    exc,
                )

    return None


async def get_json(url: str) -> Optional[dict]:
    async with httpx.AsyncClient(timeout=TIMEOUT, verify=_get_ssl_context()) as client:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                resp = await client.get(url)
                resp.raise_for_status()
                return resp.json()
            except (httpx.HTTPError, ValueError) as exc:
                logger.warning(
                    "GET %s failed (attempt %d/%d): %s",
                    url,
                    attempt,
                    MAX_ATTEMPTS,
                    exc,
                )

    return None
