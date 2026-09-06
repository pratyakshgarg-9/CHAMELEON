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
  runs an equivalent container. Stateful: if the container has a named
  Docker volume, its data rides along as a second part of the same
  multipart request and is restored before the destination container
  starts — see "Stateful migration" in `node-agent-CLAUDE.md`. Needs a
  local Docker daemon — see "Trying migration locally" below.
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
  ping mechanism. "Highest node_id wins" uses a natural-sort comparison
  (`app/node_id.py`) rather than plain string comparison, so `edge10`
  correctly outranks `edge2` once a cluster grows into double digits. On
  becoming leader (not on every re-verification tick), a node also calls
  `POST {COORDINATOR_URL}/coordinator/register` — see "Coordinator
  escalation" below.

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
docker volume create migration-demo-data
docker run -d --name migration-demo -v migration-demo-data:/data busybox sleep 600
docker exec migration-demo sh -c "echo 'hello from before the move' > /data/testfile.txt"

curl -X POST http://localhost:8000/migrate-out -H "Content-Type: application/json" \
  -d "{\"container_name\":\"migration-demo\",\"destination_node\":\"regA-c1-edge2\"}"

docker ps -a --filter name=migration-demo   # same name, new container ID
docker exec migration-demo cat /data/testfile.txt   # same content — the volume moved too
```

The volume mount (`-v migration-demo-data:/data`) is what makes this a
stateful migration — see "Stateful migration" in `node-agent-CLAUDE.md`
for exactly how the data gets from one node to the other.

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

## Coordinator escalation

Per `/shared/CONTRACT.md`: "Regional cluster leader → Global Coordinator
(`/coordinator/register`, `/coordinator/escalate`) — only when local
migration options are exhausted." Both are wired:
- `app/election.py`'s `_become_leader` registers with the coordinator once
  per actual leadership transition (not every re-verification tick).
- `app/scheduler.py`'s `_attempt_migration` escalates when there's truly no
  valid local target — no reachable neighbors at all, or the advisor
  evaluated what was available and returned `"none"`. An unreachable
  *advisor* is a different failure (network issue, not "exhausted") and
  does not escalate.

## mTLS

`MTLS_ENABLED` (default `false`, plain HTTP) gates client-cert-based auth on
both ends — outbound calls in `app/clients.py`, and the server itself via
`python main.py`'s `uvicorn.run(...)` (not the bare `uvicorn main:app` CLI,
which can't take SSL settings from `.env`). `advisor`, `trust-service`, and
`coordinator` each have the equivalent server-side setup (they never call
each other, so only need to require/verify a caller's cert, not present
one) — same `MTLS_ENABLED`/`CA_CERT_PATH`/`SERVER_CERT_PATH`/
`SERVER_KEY_PATH` env vars, same pattern.

**Real setup, all 4 services from one shared CA** (repo root):
```bash
python scripts/issue_certs.py --all
```
Issues `shared/certs/ca.crt`/`.key` (created once, reused after) plus a
cert/key for node-agent, advisor, trust-service, and coordinator, each into
their own `certs/` directory. Set `MTLS_ENABLED=true` in each service's env
(node-agent's `.env`; the other three via their process's environment) —
`CA_CERT_PATH` already defaults to `../shared/certs/ca.crt` everywhere, so
nothing else needs pointing anywhere.

This is a deadline-driven stand-in for `/shared/certs/README_2.md`'s real
process (only Member 3 ever holds the CA key, everyone else sends her a
CSR) — that assumes a multi-day exchange this project didn't have time for
before review. `ca.key` never leaves this machine and is gitignored;
`ca.crt` is committed, matching README_2.md's own "safe to commit" policy
for the public cert. Documented trade-off, not a quiet workaround.

**Local node-agent-only testing** (no need for the other three services
checked out): `python scripts/generate_dev_certs.py --node-id
regA-c1-edge1` — a separate throwaway CA scoped to just this service, same
idea, smaller footprint.

Either way: a request without a cert signed by the CA in use gets rejected
at the TLS handshake, before any route code runs — verified live, and
`tests/test_mtls.py` covers it against a real socket. **Known limitation**:
enforcement is CA-trust-only — there's no per-request check that a cert's
CN matches the caller's claimed `node_id`, because uvicorn's default
transport doesn't expose the peer cert to route handlers without a custom
protocol. See `node-agent-CLAUDE.md`'s mTLS section and
`/shared/CONTRACT.md` for the full note — deferred as a scoped, documented
decision given the review deadline, not dropped.

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

- mTLS: on and enforced across all 4 services (off by default, `MTLS_ENABLED`
  — see "mTLS" above), from one shared CA generated for this deadline rather
  than Member 3's originally-planned per-node CSR process (documented
  trade-off, see above). Enforcement is CA-trust-only, not per-request
  CN-to-node_id verification — a smaller guarantee than CONTRACT.md's full
  "CN must equal the sender's `node_id`" wording, flagged there as a
  deliberate, time-boxed scope call, not dropped.
- Migration now handles one named volume's data (see "Stateful migration"
  in `node-agent-CLAUDE.md`) — a container with more than one volume only
  has its first migrated. No handling for concurrent writes during the
  migration window, or for very large volumes (the whole thing is buffered
  in memory on both ends, same as the existing image transfer already does).
- The scheduler always targets a single fixed `MANAGED_CONTAINER_NAME` —
  there's no policy for picking among several containers on a node.
- Coordinator escalation (see above) is wired, but the coordinator itself is
  still a skeleton — it accepts and acknowledges registrations/escalations,
  it doesn't yet act on them (e.g. finding cross-cluster capacity). That's
  Member 3's side of this integration point.

All six components from `node-agent-CLAUDE.md`'s build order are now built.
