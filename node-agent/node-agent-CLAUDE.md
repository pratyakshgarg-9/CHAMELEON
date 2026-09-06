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
- [ ] Two nodes on separate VMs register and heartbeat, real latency logged
- [ ] A container migrates A -> B and keeps serving requests after
- [ ] Killing the leader process triggers re-election within the timeout
- [ ] All of the above pass using only the stubs above

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

## mTLS: known, deliberate limitation (not an oversight)

`MTLS_ENABLED=true` gives real mutual TLS — every inter-service call
requires and verifies a cert signed by the shared CA
(`shared/certs/ca.crt`, issued via `scripts/issue_certs.py`). What it does
**not** do: check that a cert's CN matches the caller's claimed `node_id`.
Enforcement is CA-trust-only (any cert our CA signed is accepted), not
per-identity.

Why: uvicorn's default ASGI transport doesn't expose the peer certificate
to route handlers (verified empirically — `request.scope["extensions"]` is
empty on a real mTLS connection). Adding the CN check would mean writing a
custom uvicorn/asyncio Protocol, real untested infrastructure, under a
tight pre-review deadline. Decided against building it right now — CA-trust
is still a real security boundary (verified live: a request with no cert,
or a cert from an unrelated CA, is rejected at the TLS handshake before any
route code runs), just not the full per-request identity check
CONTRACT.md's mTLS section describes. See the `# TODO(mTLS-CN-check)`
comments at the exact `ssl_cert_reqs=ssl.CERT_REQUIRED` call sites in
`main.py`, `advisor/app.py`, `trust-service/app.py`, `coordinator/app.py`
for where this would extend, not replace, the existing setup.
