"""demo-app — minimal stdlib-only HTTP server for the live migration demo.

Not owned by any one member's component; exists only to give the
automatic overload -> migration pipeline something real to serve and
observe through a move. GET / increments a counter persisted at
/data/counter.txt (meant to be a migrated named volume) and returns it
alongside this container's hostname:

  {"served_by": "<container hostname>", "count": N}

Migrating this container gives you both signals at once: "served_by"
changes (proves it's genuinely a new container, not the same one), and
"count" keeps climbing from where it left off instead of resetting
(proves the volume — and therefore the state — moved too, exercising
the stateful-migration path, not just the image).
"""

import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

COUNTER_PATH = Path("/data/counter.txt")


def _next_count() -> int:
    try:
        count = int(COUNTER_PATH.read_text().strip()) + 1
    except (FileNotFoundError, ValueError):
        count = 1
    COUNTER_PATH.parent.mkdir(parents=True, exist_ok=True)
    COUNTER_PATH.write_text(str(count))
    return count


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        import json

        body = json.dumps({"served_by": socket.gethostname(), "count": _next_count()}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass  # keep container logs to the migration story, not access logs


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 5000), Handler).serve_forever()
