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
- **Advisor/trust stubs**: running natively (no Docker) on edge1 —
  `~/chameleon/node-agent/.venv`, started via
  `uvicorn stubs.trust_stub:app --host 0.0.0.0 --port 8200` /
  `stubs.advisor_stub:app --host 0.0.0.0 --port 8100`, backgrounded with
  `sudo setsid bash -c '... > /tmp/X.log 2>&1 < /dev/null &'` (plain
  `nohup ... &` over SSH gets killed when the SSH session closes — `setsid`
  actually detaches it). Not managed by systemd/docker-compose yet; if
  edge1 reboots, these need restarting manually.

## Redeploying after a code change

No CI/CD — updates are pushed by hand per instance:
```bash
scp -i ~/.ssh/chameleon-nodes.pem <changed files> ubuntu@<ip>:~/chameleon/node-agent/<path>
ssh -i ~/.ssh/chameleon-nodes.pem ubuntu@<ip> "cd ~/chameleon/node-agent/deploy && sudo docker compose up -d --build"
```
Or `git pull` on the instance instead of `scp`, once local changes are
pushed to `origin/main`.

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
then `docker compose up -d --build` from this directory.

## Verify

```bash
curl http://<any-tailscale-ip>:8000/health
curl http://<any-tailscale-ip>:8000/neighbors   # latency_ms = real inter-VM RTT
curl http://<any-tailscale-ip>:8000/leader      # should converge to one node
```

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
- mTLS (per `/shared/certs/README_2.md`) is still not wired in — traffic
  between nodes is plain HTTP, riding on Tailscale's own WireGuard
  encryption. Real mTLS remains blocked on Member 3 issuing certs.

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
