# CHAMELEON dashboard

One live page for the whole mesh — node health and load, current leader, where each
application container is running, and a merged, timestamped event log (elections,
node failures, migrations, overloads, rejected identities). Light theme, no build step.

## How it works
- Polls `GET /status` on every node (a new node-agent endpoint) every 2s. One request
  per node means "didn't answer" is an unambiguous DOWN signal.
- `mesh.py` is a pure state machine (no network, no clock) so every transition is unit
  tested: `python -m pytest tests/`.
- Events come from two places. Nodes report what only they can know (election won,
  migration, overload, rejected identity). The dashboard derives what only an outside
  observer can: a node stopped answering, the cluster's leader changed. A dead node can't
  announce its own death. Derived events are back-dated to when the node actually stopped
  answering, so the log reads in true order.
- History lives in memory; restarting the dashboard clears it.

## Run on AWS (edge1)
```bash
python scripts/issue_certs.py --node-id dashboard --out-dir dashboard/certs   # on the machine holding the CA key
# copy dashboard/ to edge1, then in deploy/edge1-services/.env set DASHBOARD_NODES=...
cd deploy/edge1-services && sudo docker compose --profile mtls up -d --build dashboard
```
It binds `127.0.0.1:8080` only. View it from your laptop through a tunnel — no AWS port is opened:
```bash
ssh -i ~/.ssh/chameleon-nodes.pem -L 8080:127.0.0.1:8080 ubuntu@<edge1-public-ip>
# then open http://localhost:8080
```

## Run locally against local nodes
```bash
NODES="regA-c1-edge1=http://127.0.0.1:8001,regA-c1-edge2=http://127.0.0.1:8002" python app.py
```
Config: `NODES`, `POLL_INTERVAL_SECONDS` (2), `HOST`/`PORT` (127.0.0.1:8080), and for mTLS
`MTLS_ENABLED`, `CA_CERT_PATH`, `CLIENT_CERT_PATH`, `CLIENT_KEY_PATH`.
