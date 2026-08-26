# /shared

This is the single source of truth for everything that crosses a service boundary
in CHAMELEON. If a piece of information is needed by more than one member's
service, it lives here — not duplicated in each person's own notes.

## What goes here
- `CONTRACT.md` — human-readable contract: ports, naming conventions, timeouts,
  auth rules. Read this first.
- `/schemas` — the exact JSON shape of every request/response that crosses a
  service boundary, as JSON Schema files. Language-agnostic on purpose, since we're
  not all using the same stack.
- `/config` — a `.env.example` template so everyone's local env vars use the same
  names.
- `/certs` — the CA's public certificate and instructions for getting a per-node
  cert from Member 3. Private keys never go here (see certs/README.md).

## Update policy
If your service needs a field added/renamed/removed from a shared schema:
1. Change it here first — update the relevant file in `/schemas` and bump the
   "Last updated" line in `CONTRACT.md`.
2. Message the team before you write code against the new shape, not after.
3. Only then update your own service to match.

Never let a schema drift silently between what's written here and what your
service actually sends/expects — that mismatch is the single most common
integration bug in a 3-service system like this.
