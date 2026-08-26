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
- [ ] `/advisor` real service exists — using stub for now
- [ ] `/trust-service` real service exists — using stub for now
- [ ] mTLS certs issued by Member 3 — not yet wired in

## When in doubt
If a task would require changing anything in this file's contract section, stop and
flag it to me instead of just proceeding — that's exactly the kind of silent change
that causes integration breaks with the other two services.
