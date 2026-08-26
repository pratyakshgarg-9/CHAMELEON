# /shared/certs

## What goes here
- `ca.crt` — the CA's public certificate. Every service needs this to verify who
  it's talking to. Safe to commit to the repo.

## What never goes here (or anywhere in git)
- The CA's **private key** — only Member 3 should hold this, offline or in a
  secrets manager, never committed.
- Any individual node's **private key** (`*.key` files) — each node generates its
  own key locally and only sends Member 3 a certificate signing request (CSR),
  getting back a signed cert. The private key itself never leaves the node.

## How to get a cert for your node/service
1. Generate a keypair and CSR locally (Member 3 will share the exact OpenSSL
   command once the CA is set up).
2. Send Member 3 the CSR (not the private key).
3. Member 3 signs it and sends back your node's certificate.
4. Put your cert + key in your own service's local `./certs/` folder (not here —
   this folder is public/shared, yours is private to your machine) and point
   `CLIENT_CERT_PATH` / `CLIENT_KEY_PATH` in your `.env` at them.
