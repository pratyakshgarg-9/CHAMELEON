import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.clients import post_json
from app.config import settings
from app.heartbeat_loop import run_heartbeat_loop
from app.neighbors import PeerRegistry, load_neighbors
from app.routes import health, heartbeat, neighbors_route, register, stats_route
from app.stats import run_cpu_sampler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


async def _announce_to_neighbors(registry: PeerRegistry) -> None:
    """Best-effort peer bootstrap announce (component 1 semantics, per plan):
    tell every configured neighbor about this node right away instead of
    waiting for the first heartbeat. Doesn't block startup and doesn't retry
    beyond the shared 2-retry convention — a neighbor that's down will pick
    this node up when it announces itself in return, or once heartbeating
    (component 3) exists.
    """
    self_payload = {
        "node_id": settings.NODE_ID,
        "region": settings.REGION,
        "cluster": settings.CLUSTER,
        "address": settings.SELF_URL,
    }
    for peer in registry.list_all():
        result = await post_json(f"{peer.url}/register", self_payload)
        if result is not None:
            logger.info("registered with neighbor %s", peer.node_id)
        else:
            logger.warning("could not reach neighbor %s at %s for startup registration", peer.node_id, peer.url)


@asynccontextmanager
async def lifespan(app: FastAPI):
    registry = PeerRegistry(load_neighbors(settings.NEIGHBORS_FILE))
    app.state.registry = registry

    sampler_task = asyncio.create_task(run_cpu_sampler())
    announce_task = asyncio.create_task(_announce_to_neighbors(registry))
    heartbeat_task = asyncio.create_task(run_heartbeat_loop(registry))

    yield

    for task in (sampler_task, announce_task, heartbeat_task):
        task.cancel()
    for task in (sampler_task, announce_task, heartbeat_task):
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
