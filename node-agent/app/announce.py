import asyncio
import logging

from app.clients import post_json
from app.config import settings
from app.neighbors import PeerRegistry

logger = logging.getLogger(__name__)


async def run_announce_loop(registry: PeerRegistry) -> None:
    """Periodically (re-)announces this node to every known neighbor via
    POST /register — idempotent, safe to repeat. A one-shot announce at
    startup isn't enough: if a neighbor boots later than this node (common
    when nodes are brought up one at a time, e.g. sequential deploys), the
    one-shot attempt fails before that neighbor even exists, and nothing
    ever retries — leaving that neighbor permanently unaware this node
    exists. That gap surfaced as leader election never converging on a
    real multi-VM deployment: election only considers peers that have
    successfully /register'ed, so a late-joining node with an empty
    inbound-registration list never had anyone to announce leadership to.
    """
    self_payload = {
        "node_id": settings.NODE_ID,
        "region": settings.REGION,
        "cluster": settings.CLUSTER,
        "address": settings.SELF_URL,
    }
    while True:
        try:
            for peer in registry.list_all():
                result = await post_json(f"{peer.url}/register", self_payload)
                if result is not None:
                    logger.debug("registered with neighbor %s", peer.node_id)
                else:
                    logger.warning("could not reach neighbor %s at %s to (re-)announce", peer.node_id, peer.url)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("announce cycle failed — will retry next cycle")
        await asyncio.sleep(settings.HEARTBEAT_INTERVAL_SECONDS)
