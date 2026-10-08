import threading
import uuid
from collections import deque
from datetime import datetime, timezone
from typing import Any

from app.config import settings

# Identifies this process's run: seq restarts at 1 whenever the agent
# restarts, so a reader holding a cursor from before needs to know to reset.
BOOT_ID = uuid.uuid4().hex[:8]


class EventLog:
    """In-memory ring buffer of things this node itself knows happened
    (elections won, migrations, overloads, rejected identities). A dead
    node can't report its own death, so "node down" is deliberately not
    recorded here — the dashboard derives that from missed polls.
    """

    def __init__(self, maxlen: int = 500):
        self._lock = threading.Lock()
        self._events: deque = deque(maxlen=maxlen)
        self._seq = 0

    def record(self, type: str, message: str, **data: Any) -> dict:
        with self._lock:
            self._seq += 1
            event = {
                "seq": self._seq,
                "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
                "node_id": settings.NODE_ID,
                "type": type,
                "message": message,
                "data": data,
            }
            self._events.append(event)
            return event

    def since(self, seq: int) -> list:
        with self._lock:
            return [e for e in self._events if e["seq"] > seq]

    @property
    def latest_seq(self) -> int:
        with self._lock:
            return self._seq


event_log = EventLog()


def record(type: str, message: str, **data: Any) -> dict:
    return event_log.record(type, message, **data)
