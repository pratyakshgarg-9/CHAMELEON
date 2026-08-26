# CHAMELEON — Shared Contract

Last updated: 2026-08-26 (bump this date whenever anything below changes)

## Port allocation
| Service | Owner | Port |
|---|---|---|
| Node Agent | Member 1 | 8000 |
| AI Advisor | Member 2 | 8100 |
| Trust Service | Member 3 | 8200 |
| Global Coordinator | Member 3 | 9000 |

## Node ID convention
`<region>-<cluster>-<node-number>` e.g. `regA-c1-edge1`.
Never use raw IPs as IDs — they change if a VM restarts; the ID must stay stable.

## Message shapes
See `/schemas` for the exact, machine-checkable JSON Schema of each payload below.
- `stats_payload` — produced by Node Agent, consumed by AI Advisor & Trust Service
- `recommend_request` / `recommend_response` — Node Agent <-> AI Advisor
- `trust_score_response` — Node Agent <-> Trust Service

## Conventions everyone follows
- **Timeouts:** all inter-service HTTP calls use a 3s connect / 5s read timeout,
  2 retries, then fail safe (skip the action this cycle rather than crash).
- **Auth:** every request between services carries an mTLS client certificate;
  the certificate's CN must equal the sender's `node_id`. Requests without a
  valid cert are rejected, not logged-and-allowed.
- **Config:** every service reads these env var names, not custom equivalents —
  `NODE_ID`, `REGION`, `CLUSTER`, `ADVISOR_URL`, `TRUST_URL`, `CA_CERT_PATH`.
- **No hardcoded IPs or ports** in any service's code — always read from config.
- **Timestamps** are ISO 8601 UTC, e.g. `2026-09-05T10:15:00Z`.

## Who calls whom
- Node Agent -> AI Advisor (`/recommend`)
- Node Agent -> Trust Service (`/trust/score/{node_id}`, `/trust/report`)
- AI Advisor does **not** call Trust Service directly — trust scores arrive
  already embedded in the `stats_payload` that Node Agent sends it. Don't build
  a second dependency path here.
- Regional cluster leader -> Global Coordinator (`/coordinator/register`,
  `/coordinator/escalate`) — only when local migration options are exhausted.

## Failure vs. malice (Trust Service's call, but everyone should understand it)
A node that drops out because its VM restarted is not the same as a node that's
repeatedly lying about its resources or failing auth. Trust scoring must treat
these differently — a transient failure shouldn't tank a node's score the same
way a pattern of bad behavior does.
