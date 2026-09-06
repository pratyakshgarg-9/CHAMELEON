import asyncio
import logging
import ssl
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
    # `uvicorn main:app` CLI, so mTLS settings come from .env like everything
    # else — no separate flags to keep in sync across local/dev/deploy.
    # tests/conftest.py's TestClient(app) never goes through this path.
    import uvicorn

    run_kwargs = {"host": settings.HOST, "port": settings.PORT}

    if settings.MTLS_ENABLED:
        run_kwargs.update(
            ssl_certfile=settings.CLIENT_CERT_PATH,
            ssl_keyfile=settings.CLIENT_KEY_PATH,
            ssl_ca_certs=settings.CA_CERT_PATH,
            ssl_cert_reqs=ssl.CERT_REQUIRED,
        )

    uvicorn.run(app, **run_kwargs)
