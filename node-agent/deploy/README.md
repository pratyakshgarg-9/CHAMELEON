# Deploying node-agent to real VMs (AWS + Tailscale)

Per root-CLAUDE.md's deployment target: 3 EC2 `t3.micro` instances in
`ap-south-1`, meshed via Tailscale, each running node-agent as a Docker
container. See `../README.md` for local dev — this is for the real
multi-VM deployment.

## AWS resources already created (2026-08-27)

- **Key pair**: `chameleon-nodes` — private key at
  `C:\Users\praty\.ssh\chameleon-nodes.pem` (never commit this)
- **Security group**: `chameleon-nodes` (`sg-04627535595772513`) in the
  default VPC (`vpc-0b81af1da0065e113`) — inbound SSH (22) only from the
  admin's IP at creation time. Tailscale mesh traffic doesn't need this
  security group opened further (see "Networking" below).
- **AMI**: Ubuntu 22.04 LTS, `ami-002a6ae76416021fe`
  (`ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-20260826`)
- **Launch blocked**: hit AWS's new-account region verification
  (`PendingVerification`) on first `run-instances` attempt — waiting on
  their email before instances can actually launch.

## Step 1 — Launch the 3 instances (once account verification clears)

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

`user-data.sh` (this directory) installs Docker, Tailscale, and git at
boot — no manual setup needed for those.

Then get public IPs for SSH:
```bash
aws ec2 describe-instances --instance-ids <id1> <id2> <id3> \
  --region ap-south-1 \
  --query 'Reservations[].Instances[].[InstanceId,PublicIpAddress]' --output table
```

## Step 2 — Join each instance to the tailnet

SSH into each (`ssh -i ~/.ssh/chameleon-nodes.pem ubuntu@<public-ip>`) and:
```bash
sudo tailscale up
```
This prints an auth URL — open it in a browser and approve. Then note this
host's Tailscale IP:
```bash
tailscale ip -4
```
Collect all 3 Tailscale IPs before moving on — they go into each other's
`neighbors.yaml`.

## Step 3 — Deploy node-agent on each instance

```bash
git clone <this-repo-url> chameleon
cd chameleon/node-agent
cp .env.example .env
```

Edit `.env` on **each** instance for its own identity:
- `NODE_ID` — `regA-c1-edge1` / `edge2` / `edge3`, one per instance
- `SELF_URL` — `http://<this-instance's-tailscale-ip>:8000`
- `ADVISOR_URL` / `TRUST_URL` — still pointing at local stubs unless
  Members 2/3 have real services reachable on the tailnet; run the stubs
  on one instance (`uvicorn stubs.trust_stub:app --port 8200` /
  `advisor_stub:app --port 8100`, no Docker needed for these) and point
  every node at its Tailscale IP if so
- Leave `HEARTBEAT_INTERVAL_SECONDS`, `SUSTAINED_POLLS`, etc. at their
  defaults, or tighten them like the local multi-node verification runs did

Edit `neighbors.yaml` on each instance to list the other two by Tailscale
IP:
```yaml
neighbors:
  - node_id: regA-c1-edge2
    url: http://<edge2-tailscale-ip>:8000
  - node_id: regA-c1-edge3
    url: http://<edge3-tailscale-ip>:8000
```

Then, from `node-agent/deploy/`:
```bash
docker compose up -d --build
```

## Step 4 — Verify

Same checks as the local multi-node runs in `../README.md`, just now
against real hosts:
```bash
curl http://<any-tailscale-ip>:8000/health
curl http://<any-tailscale-ip>:8000/neighbors   # latency_ms should show real inter-VM RTT
curl http://<any-tailscale-ip>:8000/leader      # should converge to one node
```

This is the first time migration/heartbeat/election run against genuinely
separate Docker daemons — the local dev verification always shared one
daemon (documented as a known caveat in `../README.md`), so watch this run
closely rather than assuming it behaves identically.

## Networking notes

- Tailscale uses WireGuard over UDP (default port 41641) and falls back to
  DERP relay if that's blocked — works fine through the security group as
  configured (SSH-only) via relay, just with a small latency penalty
  compared to a direct connection. Opening UDP 41641 inbound in the
  security group would allow direct connections if that matters later.
- Port 8000 (node-agent) is intentionally **not** opened in the AWS
  security group — all inter-node traffic goes over the tailnet, which has
  its own encrypted, authenticated point-to-point connectivity independent
  of the AWS network layer.
- mTLS (per `/shared/certs/README_2.md`) is still not wired in — traffic
  between nodes is plain HTTP, but riding on Tailscale's own WireGuard
  encryption. Real mTLS remains blocked on Member 3 issuing certs.

## Cost note

3x `t3.micro` in `ap-south-1` running continuously is a small but real
cost (not covered by AWS's free tier, which is one instance for 12
months) — stop instances (`aws ec2 stop-instances --instance-ids ...`)
between test sessions rather than leaving them running.
