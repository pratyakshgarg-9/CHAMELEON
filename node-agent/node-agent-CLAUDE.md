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

## Style
- Type hints on all function signatures.
- Keep endpoint handlers thin — business logic (scoring thresholds, election logic)
  goes in separate modules, not inline in the FastAPI route functions.
