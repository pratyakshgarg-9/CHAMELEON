import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# Shared inter-service convention from /shared/CONTRACT.md: 3s connect / 5s
# read timeout, 2 retries, then fail safe (skip the action, don't crash).
TIMEOUT = httpx.Timeout(connect=3.0, read=5.0, write=5.0, pool=5.0)
MAX_ATTEMPTS = 3  # initial attempt + 2 retries

# TODO(mTLS): once Member 3 issues real per-node certs (see
# /shared/certs/README_2.md and root-CLAUDE.md's status checklist), pass
# cert=(settings.CLIENT_CERT_PATH, settings.CLIENT_KEY_PATH) and
# verify=settings.CA_CERT_PATH to the AsyncClient below. Plain for now so this
# runs against the local http stubs.


async def post_json(url: str, json_body: dict) -> Optional[dict]:
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                resp = await client.post(url, json=json_body)
                resp.raise_for_status()
                return resp.json()
            except (httpx.HTTPError, ValueError) as exc:
                logger.warning("POST %s failed (attempt %d/%d): %s", url, attempt, MAX_ATTEMPTS, exc)
    return None


async def post_multipart(url: str, files: dict, data: dict) -> Optional[dict]:
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                resp = await client.post(url, files=files, data=data)
                resp.raise_for_status()
                return resp.json()
            except (httpx.HTTPError, ValueError) as exc:
                logger.warning(
                    "POST %s (multipart) failed (attempt %d/%d): %s", url, attempt, MAX_ATTEMPTS, exc
                )
    return None


async def get_json(url: str) -> Optional[dict]:
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                resp = await client.get(url)
                resp.raise_for_status()
                return resp.json()
            except (httpx.HTTPError, ValueError) as exc:
                logger.warning("GET %s failed (attempt %d/%d): %s", url, attempt, MAX_ATTEMPTS, exc)
    return None
