import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)


@dataclass
class NeighborConfig:
    node_id: str
    url: str


def load_neighbors(path: str) -> list[NeighborConfig]:
    file_path = Path(path)
    if not file_path.exists():
        logger.warning("neighbors file not found at %s — starting with no neighbors", path)
        return []
    with file_path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    raw = data.get("neighbors") or []
    return [NeighborConfig(node_id=n["node_id"], url=n["url"]) for n in raw]


@dataclass
class PeerState:
    node_id: str
    url: str
    region: Optional[str] = None
    cluster: Optional[str] = None
    registered: bool = False
    last_seen: Optional[datetime] = None


class PeerRegistry:
    """In-memory registry of known/active peers, seeded from the static
    neighbors.yaml and updated as peers announce themselves (POST /register)
    or heartbeat in. No dynamic gossip — see node-agent-CLAUDE.md component 2.
    """

    def __init__(self, static_neighbors: list[NeighborConfig]):
        self._lock = threading.Lock()
        self._peers: dict[str, PeerState] = {
            n.node_id: PeerState(node_id=n.node_id, url=n.url) for n in static_neighbors
        }

    def is_configured(self, node_id: str) -> bool:
        with self._lock:
            return node_id in self._peers

    def mark_registered(
        self, node_id: str, url: str, region: Optional[str], cluster: Optional[str]
    ) -> None:
        now = datetime.now(timezone.utc)
        with self._lock:
            peer = self._peers.get(node_id)
            if peer is None:
                self._peers[node_id] = PeerState(
                    node_id=node_id,
                    url=url,
                    region=region,
                    cluster=cluster,
                    registered=True,
                    last_seen=now,
                )
            else:
                peer.url = url or peer.url
                peer.region = region or peer.region
                peer.cluster = cluster or peer.cluster
                peer.registered = True
                peer.last_seen = now

    def mark_seen(self, node_id: str) -> None:
        with self._lock:
            peer = self._peers.get(node_id)
            if peer is not None:
                peer.last_seen = datetime.now(timezone.utc)

    def list_all(self) -> list[PeerState]:
        with self._lock:
            return list(self._peers.values())
