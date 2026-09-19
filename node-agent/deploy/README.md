# Deploying node-agent to real VMs (AWS + Tailscale)

Per root-CLAUDE.md's deployment target: 3 EC2 `t3.micro` instances in
`ap-south-1`, meshed via Tailscale, each running node-agent as a Docker
container. See `../README.md` for local dev — this is for the real
multi-VM deployment. **Status: live and verified (2026-08-28)** — see
"Verification results" below.

## Current deployment

| Node | Instance ID | Public IP | Tailscale IP |
|---|---|---|---|
| regA-c1-edge1 | `i-0f4f395645577177b` | 3.110.51.181 | 100.115.98.80 |
| regA-c1-edge2 | `i-05b2768146c3a3cba` | 65.2.127.102 | 100.92.12.44 |
| regA-c1-edge3 | `i-00a0a08ea9d7256a9` | 35.154.249.104 | 100.85.175.83 |

- **Key pair**: `chameleon-nodes` — private key at
  `C:\Users\praty\.ssh\chameleon-nodes.pem` (never commit this)
- **Security group**: `chameleon-nodes` (`sg-04627535595772513`) in the
  default VPC (`vpc-0b81af1da0065e113`) — inbound SSH (22) only from the
  admin's *current* IP. **This IP changes often** (dynamic connection) —
  if SSH times out, get the current IP (`curl https://checkip.amazonaws.com`)
  and update the rule:
  ```bash
  aws ec2 revoke-security-group-ingress --group-id sg-04627535595772513 --protocol tcp --port 22 --cidr <old-ip>/32 --region ap-south-1
  aws ec2 authorize-security-group-ingress --group-id sg-04627535595772513 --protocol tcp --port 22 --cidr <new-ip>/32 --region ap-south-1
  ```
- **AMI**: Ubuntu 22.04 LTS, `ami-002a6ae76416021fe`
- **Tailnet**: under a *personal* Tailscale account (not a `vitstudent.ac.in`
  email — that domain auto-joins VIT's shared org tailnet, which was
  already at its free-tier user limit and blocked these devices from
  seeing each other at all despite each one individually authenticating).
  If SSH access changes hands, whoever manages this needs their own device
  added the same way (`sudo tailscale up` on a new node, approve the
  printed link while logged into the personal account).
- **Advisor/trust stubs**: run as two more services in `docker-compose.yml`
  (`advisor-stub` on :8100, `trust-stub` on :8200 — same image as
  node-agent, just a different uvicorn target), gated behind the `edge1`
  compose profile so only edge1 starts them:
  ```bash
  cd ~/chameleon/node-agent/deploy && sudo docker compose --profile edge1 up -d --build
  ```
  Both have `restart: unless-stopped`, so they come back on their own after
  an edge1 reboot — no manual restart needed anymore. (Superseded the
  earlier native-process/`setsid` approach; if edge1 still has those
  processes running from an older session, kill them — `pkill -f
  stubs.advisor_stub` / `pkill -f stubs.trust_stub` — before bringing the
  compose services up, so nothing fights over ports 8100/8200.)

## Redeploying after a code change

No CI/CD — updates are pushed by hand per instance:
```bash
scp -i ~/.ssh/chameleon-nodes.pem <changed files> ubuntu@<ip>:~/chameleon/node-agent/<path>
ssh -i ~/.ssh/chameleon-nodes.pem ubuntu@<ip> "cd ~/chameleon/node-agent/deploy && sudo docker compose up -d --build"
```
Or `git pull` on the instance instead of `scp`, once local changes are
pushed to `origin/main`. On edge1, add `--profile edge1` to also rebuild
the advisor/trust stub containers.

## From scratch (if these instances are ever torn down and recreated)

```bash
aws ec2 run-instances \
  --image-id ami-002a6ae76416021fe \
  --instance-type t3.micro \
  --key-name chameleon-nodes \
  --security-group-ids sg-04627535595772513 \
  --count 3 \
  --user-data file://user-data.sh \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=chameleon-node},{Key=Project,Value=CHAMELEON}]' \
  --region ap-south-1 \
  --query 'Instances[].InstanceId' --output text
```
`user-data.sh` installs Docker, Tailscale, and git at boot.

Then per instance: `sudo tailscale up` (approve the printed link while
logged into the **personal** Tailscale account, not any institutional
email), note `tailscale ip -4`, `git clone` the repo, write `.env` +
`neighbors.yaml` (see `../.env.example` for the field list — set
`SELF_URL`/`ADVISOR_URL`/`TRUST_URL` to Tailscale IPs, not public IPs),
then `docker compose up -d --build` from this directory (on whichever
instance is designated edge1, use `docker compose --profile edge1 up -d
--build` instead, to also start the advisor/trust stubs).

## Verify

```bash
curl http://<any-tailscale-ip>:8000/health
curl http://<any-tailscale-ip>:8000/neighbors   # latency_ms = real inter-VM RTT
curl http://<any-tailscale-ip>:8000/leader      # should converge to one node
```

## Live demo: automatic migration under real load (2026-09-19)

The scheduler -> advisor -> migration pipeline had code and a manual
recipe (below) but had never actually run unattended before this. Done
for real on edge1:

```bash
# Build + run the demo app (see ../../demo-app) — a tiny stdlib HTTP
# server that persists a request counter to /data/counter.txt
cd demo-app && docker build -t chameleon/demo-app .
docker volume create demo-data
docker run -d --name demo-app -v demo-data:/data -p 5000:5000 chameleon/demo-app

# In node-agent/.env: MANAGED_CONTAINER_NAME=demo-app,
# CPU_OVERLOAD_THRESHOLD=1, SUSTAINED_POLLS=2 — trips almost immediately
# on ambient CPU, no artificial load-generation needed at this threshold
docker compose --profile mtls up -d --force-recreate node-agent
```

Observed, entirely unattended, within ~20s:
```
sustained overload detected (cpu=3.8% mem=71.0%) — asking advisor for a migration target
triggering migration of demo-app to regA-c1-edge2 (advisor score=0.78: ...)
migration outcome: {'status': 'migrated', ...}
```
`curl`ing the demo app before and after showed both signals at once:
`served_by` changed to a new container hostname (genuinely a new
container), and `count` kept climbing from where it left off instead of
resetting (the volume, and therefore the state, migrated too — the
stateful-migration path, not just the image).

**Known rough edge, not fixed here** (pre-existing, unrelated to this
session's changes): the scheduler doesn't know a container it was
managing has migrated away, and keeps retrying every tick — each attempt
fails with an unhandled `ContainerNotFound` (caught at the loop level,
logged, doesn't crash, but spams errors). Clear `MANAGED_CONTAINER_NAME`
on the source node after a migration, or the receiving node should take
over managing it, whichever fits the demo. Worth fixing properly later:
`scheduler_tick` should notice the container it manages is no longer
local and stop trying, rather than relying on manual cleanup.

## Live verification: trust isolation is real, not just scored (2026-09-19)

The project's pitch is "a trust-scoring layer isolates suspicious
nodes" — previously true only in the sense that a score existed; nothing
detected bad behavior, nothing auto-isolated, and nothing actually
excluded an isolated node from anything. Closed and verified live: a
throwaway test identity (`regA-c1-test-attacker`, its own CA-signed
cert, no relation to any real node) attempted to `/register` as
`regA-c1-edge2` three times using its own cert — CN-check rejected each
one (403) and auto-reported the attempt to trust-service
(`node_id: regA-c1-test-attacker, event_type: auth_failure`). After the
third, `GET /trust/score/regA-c1-test-attacker` showed
`trust_score: 0, flags: ["auth_failure", "isolated"]` — with no manual
`/trust/isolate` call anywhere. From there:
- `advisor`'s `/recommend` disqualified it outright even when it was
  strictly better than the alternative on every other metric (5% vs 40%
  CPU/mem) — `"Excluded: regA-c1-test-attacker (isolated (trust_score=0))"`.
- The same identity attempting to self-declare as leader via
  `POST /coordinator` was rejected with 403
  (`"'regA-c1-test-attacker' is isolated and cannot be leader"`).
- A real node's own standing (edge1's) was confirmed untouched by this —
  isolation only ever attaches to the identity on the cert that actually
  misbehaved, not whoever it tried to impersonate.

## Verification results (2026-09-19, current `main` + mTLS/CN-check live)

Re-verified everything below against current code (12 commits ahead of
the 2026-08-28 run — stateful migration, mTLS mechanism, coordinator
escalation, an election node_id-sort fix), this time with
`MTLS_ENABLED=true` and CN-check enforced end to end, plus the real
advisor/trust-service/coordinator deployed to edge1 (`deploy/edge1-services/`,
replacing the stubs):

- **Convergence over real mTLS**: all 3 nodes registered/heartbeat with
  each other entirely over mutual TLS, real measured `latency_ms` 12-23ms
  (Tailscale direct or near-direct this run). `/neighbors` and `/leader`
  confirmed via `curl --cert/--key/--cacert`.
- **Migration over real mTLS**: `migration-demo` moved edge1 → edge2 via
  `/migrate-out`, confirmed gone from edge1 and running on edge2.
- **Re-election**: stopped edge3 (leader) — edge1/edge2 converged on
  edge2 within one ~3s poll; restarting edge3 reclaimed leadership within
  one further poll.
- **CN-check enforcement, live**: a `/register` claiming a `node_id` that
  doesn't match the presenting cert's CN → 403; matching → 200. Same
  confirmed on trust-service's `/trust/score/{node_id}`, coordinator's
  `/coordinator/register`, and advisor's `/recommend`.
- **Real service integration**: node-agent's `/stats` reflects
  trust-service's actual default (`trust_score: 0.5`), not the stub's
  hardcoded `1.0` — confirms it's genuinely talking to the real service.
- **Memory headroom on edge1** (t3.micro, 914MB): node-agent + 3 real
  services + 4 nginx sidecars together use ~185MB — comfortable.

To bring this mode up from a fresh instance: set `MTLS_ENABLED=true`,
`HOST=127.0.0.1`, `PORT=8001` in `.env`, switch `SELF_URL` and every
`neighbors.yaml`/`ADVISOR_URL`/`TRUST_URL`/`COORDINATOR_URL` entry to
`https://`, issue this instance's own cert (see `docker-compose.yml`'s
`mtls` profile comment), then
`docker compose --profile mtls up -d --build`. On edge1, also
`cd ../../deploy/edge1-services && docker compose --profile mtls up -d --build`
for the real advisor/trust-service/coordinator (needs their own certs
issued too, and a `.env` there per `.env.example`).

## Verification results (2026-08-28)

Full pipeline tested live against the 3 real instances above (genuinely
separate Docker daemons — the first time any of this ran outside a shared
local daemon):

- **Convergence**: all 3 nodes registered with each other and converged on
  `regA-c1-edge3` as leader (highest `node_id`), with real measured
  `latency_ms` in the 40-135ms range (DERP-relayed, `ap-south-1` region).
- **Migration**: `migration-demo` (busybox) moved from edge1 → edge2 via
  `/migrate-out`, confirmed gone from edge1 and running under a new
  container ID on edge2.
- **Missed-heartbeat re-election**: stopped edge3's container — edge1 and
  edge2 correctly detected the stale leader and converged on `edge2`
  (next-highest) within ~20s. Restarting edge3 correctly reclaimed
  leadership (highest `node_id`) once it rejoined.

Two real bugs found and fixed during this deployment (both now in
`main`):
- **Peer announce was one-shot** (`app/announce.py`, replacing the old
  `_announce_to_neighbors` in `main.py`): a node whose neighbors all
  happened to boot *after* it (real here, since each instance's Docker
  build took different wall-clock time — never an issue in local testing
  where processes start within milliseconds of each other) ended up with
  zero registered peers forever, breaking election convergence. Fixed by
  making the announce a periodic retrying loop instead of a single
  startup attempt.
- **Docker socket wasn't mounted into the container** — `docker-compose.yml`
  now mounts `/var/run/docker.sock`, which is what lets `docker.from_env()`
  inside the node-agent container manage containers on the *host's* Docker
  daemon. Never surfaced locally, where node-agent always ran as a plain
  process (never inside its own container) with direct access to Docker
  Desktop's socket.

## Networking notes

- Tailscale uses WireGuard over UDP (default port 41641) and falls back to
  DERP relay if that's blocked — works fine through the security group as
  configured (SSH-only) via relay, just with a latency penalty vs. a direct
  connection (observed 40-135ms here). Opening UDP 41641 inbound would
  allow direct connections if that matters later.
- Port 8000 (node-agent) is intentionally **not** opened in the AWS
  security group — all inter-node traffic goes over the tailnet.
- mTLS + CN-check is now **live on these instances** (2026-09-19, via the
  `mtls` compose profile) — traffic between nodes and to the real
  advisor/trust-service/coordinator (also now deployed to edge1,
  replacing the old stubs — see `deploy/edge1-services/`) is real mutual
  TLS with per-request identity verification, not just Tailscale's own
  WireGuard encryption. See `node-agent-CLAUDE.md`'s mTLS section for the
  mechanism (nginx sidecar terminating the handshake + forwarding the
  verified CN).

  Bugs found and fixed getting this live on real instances (none of these
  showed up in local/dev testing, same pattern as the migration bugs
  above):
  - **nginx:alpine doesn't recognize `$ssl_client_s_dn_cn`** ("unknown
    variable", crash loop) — extract the CN from `$ssl_client_s_dn` via a
    `map` block instead (see `nginx.conf`).
  - **`client_max_body_size` defaults to 1m** — rejected every real
    `/migrate-out` image transfer with 413; set to unlimited on
    node-agent's nginx.
  - **Python's default SSL context does hostname/IP verification** —
    every peer call failed with `CERTIFICATE_VERIFY_FAILED` because our
    certs' identity is `node_id` (CN), not a SAN for the peer's Tailscale
    IP. Fixed with `ctx.check_hostname = False` in `app/clients.py` —
    identity is verified a different way (CA-trust + the server-side CN
    check), deliberately not tied to network address.
  - **`SELF_URL`/`neighbors.yaml`/`ADVISOR_URL`/`TRUST_URL`/
    `COORDINATOR_URL` must all use `https://`**, not `http://` — httpx
    only applies the mTLS context to `https://` requests; an `http://`
    URL silently skips TLS entirely regardless of `MTLS_ENABLED`.
  - **`CA_CERT_PATH`'s default (`../shared/certs/ca.crt`, relative to
    `/app`) was never actually mounted into the node-agent container** —
    only the node's own cert was. Outbound calls need the CA cert too, to
    validate the certs presented by whatever they call; added a
    `../../shared/certs:/shared/certs:ro` mount.

## Cost note

3x `t3.micro` in `ap-south-1` running continuously is a small but real
cost (not covered by AWS's free tier, which is one instance for 12
months) — stop instances between test sessions:
```bash
aws ec2 stop-instances --instance-ids i-0f4f395645577177b i-05b2768146c3a3cba i-00a0a08ea9d7256a9 --region ap-south-1
```
and start them again with `start-instances` (same instance IDs, new public
IPs each time — Tailscale IPs stay the same, so update the security group
rule and any `.env`/`neighbors.yaml` that reference public IPs, though
this deployment only uses Tailscale IPs for inter-node config so that's
just the SSH access rule).
