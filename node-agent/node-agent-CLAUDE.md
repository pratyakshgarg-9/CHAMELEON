# node-agent — Service Context

(Root-level CLAUDE.md is also loaded automatically — this file adds my specific
scope on top of the shared contract there. Don't repeat the port table/schemas
here; refer to root.)

## My role
Infrastructure & edge networking: node agent, container migration, scheduler,
leader election. This is the plumbing the AI advisor and trust service plug into.

## Tech stack
- Python 3.11, FastAPI, uvicorn
- `psutil` for real CPU/memory stats
- `docker-py` for container start/stop/commit/save/load (migration)
- `grpcio` only if/when I decide gRPC is worth it over REST — default to REST/FastAPI
  for now, it's simpler to debug

## Components to build, in order
1. Node agent skeleton: `/register`, `/heartbeat`, `/stats`, `/neighbors`, `/health`
2. Static neighbor discovery via `neighbors.yaml` (no dynamic gossip needed at this scale)
3. Background heartbeat loop with measured round-trip latency logged per neighbor
4. Container migration: `/migrate-out` (stop, commit, save) and `/migrate-in`
   (load, run, confirm healthy)
5. Scheduler: overload threshold watcher -> calls advisor -> validates response ->
   triggers migration
6. Leader election: Bully algorithm (highest node-ID wins) within a regional cluster,
   triggered on missed heartbeats

## Stub contracts to build against until the real services exist
```
POST /recommend  (run locally as a stub)
  -> always returns {"recommended_node": "<nearest neighbor from config>", "score": 1.0}

GET /trust/score/{node_id}  (run locally as a stub)
  -> always returns {"node_id": "...", "trust_score": 1.0, "flags": []}
```
Build against the real contract shape in root CLAUDE.md even while stubbing —
swapping to the real advisor/trust-service later should be a config URL change,
not a rewrite.

## Commands
- Run locally: `uvicorn main:app --reload --port 8000`
- Run tests: `pytest`
- Build image: `docker build -t node-agent .`
- Run container: `docker run --env-file .env -p 8000:8000 node-agent`

## Testing checklist before calling anything "done"
- [x] Two nodes on separate VMs register and heartbeat, real latency logged
      (re-verified 2026-09-19 against `7cb0336` on the real 3-VM AWS
      deployment — see `deploy/README.md`; latency 15-23ms tailnet RTT)
- [x] A container migrates A -> B and keeps serving requests after
      (re-verified 2026-09-19: `migration-demo` edge1 -> edge2, confirmed
      gone from edge1, running on edge2)
- [x] Killing the leader process triggers re-election within the timeout
      (re-verified 2026-09-19: stopped edge3 (leader) -> edge2 elected
      within one ~2s poll; restarted edge3 -> reclaimed leadership within
      one ~3s poll)
- [x] All of the above pass using only the stubs above (verified above
      against the local stubs; real advisor/trust-service/coordinator
      deployment is tracked separately, see root status board)

## Failure points I've been told to watch for
- Don't change a field name in the shared stats schema without updating root
  CLAUDE.md and `/shared` first — Member 2 and 3's services depend on it exactly
  as documented.
- Don't let a slow `/recommend` call block the heartbeat loop — respect the 5s
  timeout from root CLAUDE.md and fail safe (skip migration this cycle) on timeout.
- Don't hardcode cert paths — Member 3 may rotate the CA; read from `CA_CERT_PATH`
  in `.env` so a rotation doesn't need a code change.

## Stateful migration (single named volume)

`docker commit` (what `stop_commit_remove` uses) captures image/filesystem
state but never a mounted volume's contents — closing that gap was scoped
to exactly one volume per managed container, on purpose:

- `get_run_config` now also reads the container's `Mounts` and returns a
  `volumes` map (`{volume_name: {"bind": path, "mode": rw|ro}}`) alongside
  the existing `ports`/`restart_policy`. A container with more than one
  named volume only has the first migrated (logged as a warning) — not
  handled, by design, for this pass.
- `docker_client.export_volume_data`/`import_volume_data` move the actual
  bytes via `container.get_archive`/`put_archive` — the same primitives
  `docker cp` itself uses, through a short-lived, unstarted helper
  container with the volume mounted. No `exec`, no shelling out to `tar`.
- The volume's tarball rides as a second file part (`data`) on the exact
  same multipart `POST /migrate-in` the image transfer already uses —
  deliberately not a new transport, so there's nothing new to secure,
  firewall, or reason about beyond what the existing transfer already is.
- On `/migrate-in`, the volume is restored *before* `run_container` starts
  the new container, so it never races a fresh, empty mount against the
  restore.
- Out of scope, explicitly: concurrent writes during the migration window,
  and volumes too large to buffer in memory on both ends (matches the
  existing image transfer's own limits — this doesn't make that worse, but
  doesn't fix it either).

## Style
- Type hints on all function signatures.
- Keep endpoint handlers thin — business logic (scoring thresholds, election logic)
  goes in separate modules, not inline in the FastAPI route functions.

## mTLS + CN-check (2026-09-19: closed the gap below)

`MTLS_ENABLED=true` gives real mutual TLS, now including the per-request
CN check that used to be missing: a caller's claimed `node_id` (in
`/register`, `/heartbeat`, `/election`, `/coordinator`'s request bodies)
must match the CN of the cert it actually presented, or the request is
rejected with 403 (`app/deps.py:require_cn_matches`).

The handshake itself moved out of uvicorn and into an **nginx sidecar**
in front of each service (`deploy/nginx.conf`, compose profile `"mtls"`)
— uvicorn's default ASGI transport doesn't expose the peer cert to route
handlers (confirmed empirically), so nginx terminates the mTLS connection
and forwards the verified CN as `X-SSL-Client-CN`, which
`app/deps.py:get_verified_cn` reads. Same pattern applied to
`advisor`/`trust-service`/`coordinator` (their own `deploy/nginx.conf` +
a `require_cn_matches` in each `app.py`) — see
`deploy/edge1-services/docker-compose.yml`.

Each node-agent EC2 instance needs **its own** cert (CN = its own
`node_id`) — `node-agent/certs/` is gitignored and instance-local by
design, generated on that host with
`python scripts/issue_certs.py --node-id <that instance's node_id> --out-dir node-agent/certs`,
never copied between instances (a shared cert would defeat the CN check
entirely — this was an actual bug caught before deploy, see
`scripts/issue_certs.py`'s comment on why `ALL_IDENTITIES` excludes
node-agent).

`/trust/report`'s `node_id` is deliberately **not** CN-checked — nothing
in this codebase calls it yet, and it's genuinely ambiguous whether it's
self-reported or third-party-reported; enforcing self-match here would be
a guess that could silently break the intended use. Flagged for whoever
wires it up.
