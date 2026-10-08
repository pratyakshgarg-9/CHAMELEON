"""CHAMELEON dashboard — polls every node's /status and serves one live page.

Run:  python app.py        (config entirely via env, see below)

  NODES                 "regA-c1-edge1=https://100.x.x.x:8000,regA-c1-edge2=..."
  POLL_INTERVAL_SECONDS 2
  HOST / PORT           127.0.0.1 / 8080 (reach it through an SSH tunnel)
  MTLS_ENABLED          true when the nodes sit behind their mTLS nginx sidecars
  CA_CERT_PATH / CLIENT_CERT_PATH / CLIENT_KEY_PATH   this dashboard's own cert
"""

import asyncio
import logging
import os
import ssl
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.responses import FileResponse

from mesh import MeshState

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("chameleon.dashboard")

POLL_INTERVAL = float(os.environ.get("POLL_INTERVAL_SECONDS", "2"))
REQUEST_TIMEOUT = float(os.environ.get("REQUEST_TIMEOUT_SECONDS", "2.5"))
MTLS_ENABLED = os.environ.get("MTLS_ENABLED", "false").lower() == "true"
CA_CERT_PATH = os.environ.get("CA_CERT_PATH", "/shared/certs/ca.crt")
CLIENT_CERT_PATH = os.environ.get("CLIENT_CERT_PATH", "./certs/node.crt")
CLIENT_KEY_PATH = os.environ.get("CLIENT_KEY_PATH", "./certs/node.key")
BIND_HOST = os.environ.get("HOST", "127.0.0.1")
BIND_PORT = int(os.environ.get("PORT", "8080"))

STATIC_DIR = Path(__file__).parent / "static"


def parse_nodes(raw: str) -> dict:
    nodes = {}
    for part in raw.split(","):
        part = part.strip()
        if part:
            node_id, _, url = part.partition("=")
            nodes[node_id.strip()] = url.strip().rstrip("/")
    return nodes


def _ssl_context():
    if not MTLS_ENABLED:
        return True
    ctx = ssl.create_default_context(cafile=CA_CERT_PATH)
    ctx.load_cert_chain(certfile=CLIENT_CERT_PATH, keyfile=CLIENT_KEY_PATH)
    # Nodes are addressed by Tailscale IP while certs carry node_id as CN —
    # same reasoning as node-agent/app/clients.py: CA-signature is what's verified.
    ctx.check_hostname = False
    return ctx


async def _fetch(client: httpx.AsyncClient, state: MeshState, node_id: str, url: str):
    try:
        resp = await client.get(f"{url}/status", params={"since": state.cursor(node_id)})
        resp.raise_for_status()
        return node_id, resp.json()
    except Exception as exc:  # any failure = "didn't answer"; MeshState decides what that means
        logger.debug("poll of %s failed: %s", node_id, exc)
        return node_id, None


async def poll_loop(state: MeshState, client: httpx.AsyncClient, nodes: dict) -> None:
    while True:
        try:
            results = dict(await asyncio.gather(*(_fetch(client, state, nid, url) for nid, url in nodes.items())))
            state.update(results, datetime.now(timezone.utc))
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("poll round failed — will retry")
        await asyncio.sleep(POLL_INTERVAL)


@asynccontextmanager
async def lifespan(app: FastAPI):
    nodes = parse_nodes(os.environ.get("NODES", ""))
    if not nodes:
        logger.warning("NODES is empty — nothing to monitor")
    app.state.mesh = MeshState(nodes)
    client = httpx.AsyncClient(timeout=httpx.Timeout(REQUEST_TIMEOUT), verify=_ssl_context())
    task = asyncio.create_task(poll_loop(app.state.mesh, client, nodes))
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    await client.aclose()


app = FastAPI(title="CHAMELEON Dashboard", lifespan=lifespan)


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/state")
async def api_state():
    return app.state.mesh.snapshot(datetime.now(timezone.utc))


@app.get("/health")
async def health():
    return {"status": "ok", "service": "chameleon-dashboard"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=BIND_HOST, port=BIND_PORT)
