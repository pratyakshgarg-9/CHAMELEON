import logging
from typing import Optional

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


async def post_json(url: str, json_body: dict) -> Optional[dict]:
    async with httpx.AsyncClient(
        timeout=TIMEOUT,
        cert=(settings.CLIENT_CERT_PATH, settings.CLIENT_KEY_PATH),
        verify=settings.CA_CERT_PATH,
    ) as client:
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
    async with httpx.AsyncClient(
        timeout=timeout,
        cert=(settings.CLIENT_CERT_PATH, settings.CLIENT_KEY_PATH),
        verify=settings.CA_CERT_PATH,
    ) as client:
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
    async with httpx.AsyncClient(
        timeout=TIMEOUT,
        cert=(settings.CLIENT_CERT_PATH, settings.CLIENT_KEY_PATH),
        verify=settings.CA_CERT_PATH,
    ) as client:
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