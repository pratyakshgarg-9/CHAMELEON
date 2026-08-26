# node-agent

CHAMELEON's node agent (Member 1's service) — see `node-agent-CLAUDE.md` for
scope/build order and `/shared` for the cross-service contract. This is
step 1 of that build order: the FastAPI skeleton (`/register`, `/heartbeat`,
`/stats`, `/neighbors`, `/health`) plus local stubs for the AI Advisor and
Trust Service so later components can be built against a real contract shape
before Members 2/3 have real services running.

## Local setup

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -r requirements.txt
copy .env.example .env        # then edit if you need non-default values
```

## Run it (3 processes)

```bash
# Terminal 1 — trust service stub
uvicorn stubs.trust_stub:app --port 8200

# Terminal 2 — advisor stub
uvicorn stubs.advisor_stub:app --port 8100

# Terminal 3 — the node agent itself
uvicorn main:app --reload --port 8000
```

Then:

```bash
curl http://localhost:8000/health
curl http://localhost:8000/stats
curl http://localhost:8000/neighbors
curl -X POST http://localhost:8000/register -H "Content-Type: application/json" \
  -d "{\"node_id\":\"regA-c1-edge2\",\"region\":\"regA\",\"cluster\":\"c1\",\"address\":\"http://localhost:8001\"}"
curl -X POST http://localhost:8000/heartbeat -H "Content-Type: application/json" -d "{}"
```

## Two-node local dev (to see /register's peer announce actually fire)

Run a second agent instance with a different identity and port, and list each
in the other's `neighbors.yaml`:

```bash
# second instance
set NODE_ID=regA-c1-edge2
set PORT=8001
set SELF_URL=http://localhost:8001
uvicorn main:app --port 8001
```

Add the other node to each `neighbors.yaml` (see the commented example in
that file) and restart both — each announces itself to the other on startup
and shows up in `GET /neighbors`.

## Tests

```bash
pytest
```

Includes a schema-conformance test (`tests/test_stats.py`) that validates a
live `/stats` response against `/shared/schemas/stats_payload.schema.json`
directly, so drift from the source of truth fails a test instead of going
unnoticed.

## Known gaps (intentional, deferred to later steps)

- `latency_ms` in `/stats` is always `{}` — populated once the outbound
  heartbeat *loop* (component 3) exists.
- mTLS isn't applied to outbound calls yet (`app/clients.py` has a `TODO`) —
  blocked on Member 3 issuing real per-node certs, per root-CLAUDE.md's
  status board.
- Container migration, the scheduler, and leader election (components 4-6)
  aren't built yet.
- Migration will need Docker installed locally to exercise — not required
  for anything in this step.
