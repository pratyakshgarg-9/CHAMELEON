import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.announce import run_announce_loop
from app.config import settings
from app.election import ElectionState, run_election_monitor
from app.heartbeat_loop import run_heartbeat_loop
from app.neighbors import PeerRegistry, load_neighbors
from app.routes import election, health, heartbeat, migrate, neighbors_route, register, stats_route
from app.scheduler import OverloadTracker, run_scheduler_loop
from app.stats import run_cpu_sampler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    registry = PeerRegistry(load_neighbors(settings.NEIGHBORS_FILE))
    app.state.registry = registry
    election_state = ElectionState()
    app.state.election = election_state

    sampler_task = asyncio.create_task(run_cpu_sampler())
    announce_task = asyncio.create_task(run_announce_loop(registry))
    heartbeat_task = asyncio.create_task(run_heartbeat_loop(registry))
    scheduler_task = asyncio.create_task(run_scheduler_loop(registry, OverloadTracker()))
    election_task = asyncio.create_task(run_election_monitor(registry, election_state))

    background_tasks = (sampler_task, announce_task, heartbeat_task, scheduler_task, election_task)

    yield

    for task in background_tasks:
        task.cancel()
    for task in background_tasks:
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="CHAMELEON Node Agent", lifespan=lifespan)

app.include_router(health.router)
app.include_router(stats_route.router)
app.include_router(neighbors_route.router)
app.include_router(register.router)
app.include_router(heartbeat.router)
app.include_router(migrate.router)
app.include_router(election.router)


if __name__ == "__main__":
    # Entrypoint (also what the Dockerfile CMD runs) instead of the bare
    # `uvicorn main:app` CLI, so settings come from .env like everything
    # else — no separate flags to keep in sync across local/dev/deploy.
    # tests/conftest.py's TestClient(app) never goes through this path.
    #
    # Always plain HTTP here — when MTLS_ENABLED, the mTLS handshake and
    # CN verification happen in the nginx sidecar in front of this process
    # (deploy/nginx.conf, "mtls" compose profile), which proxies to us
    # over plain HTTP on HOST/PORT (set to 127.0.0.1:8001 in that mode, so
    # nginx is the only way in). Uvicorn doing its own TLS here as well as
    # nginx doing TLS on the real external port was the old, sidecar-less
    # setup — replaced because it couldn't do a CN check (uvicorn's
    # default ASGI transport doesn't expose the peer cert to route
    # handlers) and doesn't compose with the sidecar's plain-HTTP
    # proxy_pass anyway.
    import uvicorn

    uvicorn.run(app, host=settings.HOST, port=settings.PORT)
