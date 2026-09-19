# CHAMELEON — Root Project Context

## What this is
Decentralized edge-computing mesh: edge nodes cooperate before escalating to the
cloud. Docker containers migrate securely between nodes; an AI advisor recommends
placement; a trust-scoring layer isolates suspicious nodes. Course project, semester
timeline, 3-person team.

## Who's building what (and who uses what tools)
Only **I (Member 1)** use Claude Code, scoped to `/node-agent`. My teammates build
`/advisor` (Member 2) and `/trust-service` + `/coordinator` (Member 3) with their own
tools — don't assume their code follows the same stack or style as mine.

**Never read, edit, or refactor files outside `/node-agent` and `/shared` unless I
explicitly ask.** Treat `/advisor`, `/trust-service`, `/coordinator` as external
services I only integrate with over HTTP — not code I own.

## Repo layout
```
/node-agent      <- mine, built with Claude Code
/advisor         <- Member 2, do not touch
/trust-service   <- Member 3, do not touch
/coordinator     <- Member 3, do not touch
/shared          <- schemas, certs, conventions — source of truth, all members write here
```

## Deployment target
3-4 VMs (AWS EC2), meshed via Tailscale. Each node runs the node-agent as a
Docker container. Not a single-machine simulation — real network latency
matters and should be measured, not mocked, wherever possible.

Note: AWS's free tier is 12 months and one t2.micro/t3.micro instance, not
an always-free multi-instance tier — budget/instance-count accordingly for
3-4 VMs (2026-08-27 decision, revised from an earlier Oracle Cloud plan).

## Shared contract — do not change without updating `/shared` and telling the team

### Port allocation
| Service | Owner | Port |
|---|---|---|
| Node Agent | Me | 8000 |
| AI Advisor | Member 2 | 8100 |
| Trust Service | Member 3 | 8200 |
| Global Coordinator | Member 3 | 9000 |

### Node ID convention
`<region>-<cluster>-<node-number>` e.g. `regA-c1-edge1`. Never use raw IPs as IDs.

### Shared stats payload (I produce this, others consume it)
```json
{
  "node_id": "regA-c1-edge1",
  "timestamp": "2026-09-05T10:15:00Z",
  "cpu_percent": 72.5,
  "mem_percent": 60.1,
  "latency_ms": {"regA-c1-edge2": 12.3, "regA-c1-edge3": 20.1},
  "history_load_avg_5m": 65.2,
  "trust_score": 0.92
}
```

### `/recommend` contract (Member 2's service, I call it)
Request: `{"overloaded_node": "...", "candidates": [ /* stats objects above */ ]}`
Response: `{"recommended_node": "...", "score": 0.87, "reasoning": "..."}`

### `/trust/score/{node_id}` contract (Member 3's service, I call it)
Response: `{"node_id": "...", "trust_score": 0.92, "last_updated": "...", "flags": []}`

### Conventions
- All inter-service HTTP calls: 3s connect / 5s read timeout, 2 retries.
- Every request carries an mTLS client cert; CN = node_id.
- Config via `.env`: `NODE_ID`, `REGION`, `CLUSTER`, `ADVISOR_URL`, `TRUST_URL`,
  `CA_CERT_PATH`. No hardcoded IPs/ports anywhere in code.

## Current status (update this as the project moves)
- [x] `/advisor` real service exists — merged into `main`, verified against
      the shared contract and running live (2026-09-06)
- [x] `/trust-service` real service exists — merged into `main` from
      `member3-security-trust`, verified live (2026-09-06); `/coordinator`
      skeleton also merged
- [x] mTLS wired across all 4 services, now **including** the per-request
      CN-to-`node_id` check (2026-09-19) via an nginx sidecar terminating
      the handshake in front of each service — see
      `node-agent/node-agent-CLAUDE.md`'s mTLS section for the mechanism.
      Certs re-issued with 180-day validity (`scripts/issue_certs.py`,
      expires ~2027-03) from one shared CA, still the deadline-driven
      trade-off vs. Member 3's originally-planned per-node CSR exchange
      (`/shared/CONTRACT.md`'s Auth section)
- [x] node-agent's 3-VM AWS deployment (`ap-south-1`, Tailscale mesh)
      re-verified live against current `main` (2026-09-19) — register/
      heartbeat with real latency, migration, leader re-election all
      confirmed on the real instances, not just locally
- [x] Real `advisor`/`trust-service`/`coordinator` deployed live to AWS
      (2026-09-19, on edge1, replacing the `advisor-stub`/`trust-stub`)
      — full 4-service system verified together over real mTLS +
      CN-check, not stubs. See `node-agent/deploy/README.md`'s
      2026-09-19 verification results and `deploy/edge1-services/`.
      Instances are stopped between sessions to control AWS cost — start
      with the instance IDs in that same README before a live demo.
- [x] "Trust-scoring layer isolates suspicious nodes" is now actually
      true (2026-09-19), not just a score sitting unused: a CN mismatch
      auto-reports to trust-service, repeated reports auto-isolate (no
      manual `/trust/isolate` call needed anywhere), and isolation is
      enforced in both places it matters — `advisor` hard-disqualifies
      isolated migration candidates, and node-agent's election rejects
      an isolated node both as something to defer to *and* as a
      self-declared leader. Verified live end-to-end, see
      `node-agent/deploy/README.md`'s "trust isolation is real" section.
- [x] Automatic overload -> advisor -> migration pipeline demonstrated
      live and unattended (2026-09-19) against a real tiny HTTP demo app
      (`/demo-app`), not just curled by hand — including state (a
      request counter) surviving the move via the existing
      stateful-migration path. See `node-agent/deploy/README.md`'s
      "Live demo" section for the exact repro steps before the review,
      and its noted pre-existing rough edge (scheduler doesn't notice a
      managed container has already migrated away and keeps retrying).

## When in doubt
If a task would require changing anything in this file's contract section, stop and
flag it to me instead of just proceeding — that's exactly the kind of silent change
that causes integration breaks with the other two services.
