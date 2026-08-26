# node-agent

CHAMELEON's node agent (Member 1's service) — see `node-agent-CLAUDE.md` for
scope/build order and `/shared` for the cross-service contract. Built so far:

- **Component 1** — the FastAPI skeleton (`/register`, `/heartbeat`,
  `/stats`, `/neighbors`, `/health`) plus local stubs for the AI Advisor and
  Trust Service so later components can be built against a real contract
  shape before Members 2/3 have real services running.
- **Component 3** — the outbound background heartbeat loop
  (`app/heartbeat_loop.py`): periodically pings every known neighbor's
  `/heartbeat` and records round-trip latency, which now flows into both
  `GET /stats`'s `latency_ms` and `GET /neighbors`.
- **Component 4** — container migration (`app/docker_client.py`,
  `app/migration.py`, `app/routes/migrate.py`): `POST /migrate-out`
  stops+commits+removes a local container, ships the image straight to the
  destination's `POST /migrate-in` over HTTP (multipart), which loads it and
  runs an equivalent container. Needs a local Docker daemon — see "Trying
  migration locally" below.
- **Component 5** — the scheduler (`app/scheduler.py`): a background loop
  watches `cpu_percent`/`mem_percent`; once either stays over its threshold
  for `SUSTAINED_POLLS` consecutive polls, it fetches every neighbor's live
  `/stats`, asks the advisor for a recommendation, validates the response
  (must be a real candidate or the literal `"none"` — never trusted blindly),
  and triggers a real migration of `MANAGED_CONTAINER_NAME` via the same
  logic `/migrate-out` uses. Inactive unless `MANAGED_CONTAINER_NAME` is set.
- **Component 6** — leader election (`app/election.py`, `app/routes
  /election.py`): Bully algorithm, highest `node_id` wins, scoped to peers
  sharing this node's `region`+`cluster`. `POST /election`/`POST
  /coordinator` implement the protocol; `GET /leader` reports what this node
  currently believes. Re-verifies every `ELECTION_CHECK_INTERVAL_SECONDS`
  tick even when this node already believes itself leader — deliberately,
  since a node can trivially win its own bootstrap election before it's
  finished learning about peers, and without periodic re-verification that
  race leaves a permanent split-brain (found via a live 3-node run; see the
  comment on `check_leader_liveness`). Piggybacks on the component-3
  heartbeat loop's `last_seen` data to detect a dead leader — no separate
  ping mechanism.

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
and shows up in `GET /neighbors`. After one `HEARTBEAT_INTERVAL_SECONDS`
(default 10s), `GET /stats` and `GET /neighbors` on either node will show a
real measured `latency_ms` to the other.

## Trying migration locally

Needs a running Docker daemon (Docker Desktop). Both node-agent instances
below share your one local daemon, so this proves the HTTP + migration
mechanics for real, just not literally-separate-daemon behavior — good
enough for local dev, not a substitute for a real multi-VM check later.

```bash
docker run -d --name migration-demo busybox sleep 600

curl -X POST http://localhost:8000/migrate-out -H "Content-Type: application/json" \
  -d "{\"container_name\":\"migration-demo\",\"destination_node\":\"regA-c1-edge2\"}"

docker ps -a --filter name=migration-demo   # same name, new container ID
```

To see the scheduler trigger this automatically instead of curling it by
hand, start a node with `MANAGED_CONTAINER_NAME=migration-demo` and an
artificially low `CPU_OVERLOAD_THRESHOLD`/`MEM_OVERLOAD_THRESHOLD` (e.g. `1`)
so real background CPU usage trips it within a few polls — watch the logs
for `sustained overload detected` → `triggering migration of ...` →
`migration outcome: ...`.

## Trying leader election locally

Start 3 instances (three `NODE_ID`/`PORT`/`SELF_URL` triples, each
`neighbors.yaml` listing the other two — same pattern as the two-node setup
above). Poll `GET /leader` on each; they converge on whichever has the
highest `node_id`. Kill that process and poll the survivors again — they
re-elect among themselves within roughly `HEARTBEAT_INTERVAL_SECONDS *
MISSED_HEARTBEATS_BEFORE_ELECTION + ELECTION_CHECK_INTERVAL_SECONDS` of the
kill. Lowering those three env vars (e.g. to `3`/`2`/`2`) makes this fast
enough to watch live instead of waiting on the defaults.

## Tests

```bash
pytest
```

Includes a schema-conformance test (`tests/test_stats.py`) that validates a
live `/stats` response against `/shared/schemas/stats_payload.schema.json`
directly, so drift from the source of truth fails a test instead of going
unnoticed. `tests/test_docker_client.py` (marked `docker`) exercises the
real migration mechanics against a real container — needs Docker running;
`tests/test_migrate_routes.py` covers the HTTP layer with Docker calls
mocked, so the rest of the suite doesn't need Docker at all.

## Known gaps (intentional, deferred to later steps)

- mTLS isn't applied to outbound calls yet (`app/clients.py` has a `TODO`) —
  blocked on Member 3 issuing real per-node certs, per root-CLAUDE.md's
  status board.
- Migration doesn't handle volumes/mounts — only port bindings and restart
  policy are captured and reapplied. Migrating stateful volume data is a
  bigger problem than this build addresses.
- The scheduler always targets a single fixed `MANAGED_CONTAINER_NAME` —
  there's no policy for picking among several containers on a node.
- Leader election uses plain string comparison on the full `node_id` for
  "highest wins" — correct at this project's 3-4 VM scale, but a 10+ node
  cluster would need numeric suffix comparison to sort correctly (e.g.
  `edge10` < `edge2` as strings).
- No node currently *uses* the elected leader for anything — component 6 is
  the election mechanism itself; wiring leadership into the scheduler or
  coordinator escalation (`/coordinator/register`, `/coordinator/escalate`
  per root-CLAUDE.md) is a separate, later integration.

All six components from `node-agent-CLAUDE.md`'s build order are now built.
