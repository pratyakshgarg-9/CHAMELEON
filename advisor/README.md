# CHAMELEON — AI Advisor (Member 2)

Scoring service that recommends the best target node for migrating a workload
off an overloaded node. It does **not** perform the migration — Member 1's
scheduler makes the final call using this service's recommendation.

> **Schema note:** `/shared/CONTRACT.md` and `/shared/schemas/` were not
> available in this workspace when this service was written. `models.py`
> defines a documented placeholder schema based on the fields in the
> project spec (CPU/memory headroom, latency, trust, 5-min historical
> load). If the team's real shared schema differs, `models.py` is the only
> file that should need updating — `scorer.py`, `app.py`, `generator.py`,
> and `evaluator.py` all consume the `CandidateStats` type, not raw dicts.

---

## Install

```bash
cd advisor
python3 -m venv venv && source venv/bin/activate   # optional but recommended
pip install -r requirements.txt
```

## Start the service

```bash
uvicorn app:app --host 0.0.0.0 --port 8100
```

The service listens on **port 8100**.

## Call `/health`

```bash
curl http://localhost:8100/health
```

```json
{"status": "ok", "service": "chameleon-ai-advisor"}
```

## Call `/recommend`

### Example request

```bash
curl -X POST http://localhost:8100/recommend \
  -H "Content-Type: application/json" \
  -d '{
    "request_id": "demo-1",
    "source_node": "regA-c1-edge1",
    "candidates": [
      {
        "node_id": "regA-c1-edge2",
        "cpu_usage_percent": 20,
        "memory_usage_percent": 30,
        "latency_ms": 15,
        "trust_score": 0.95,
        "historical_load_5min": 25,
        "last_updated": 1735689600
      },
      {
        "node_id": "regA-c1-edge3",
        "cpu_usage_percent": 88,
        "memory_usage_percent": 80,
        "latency_ms": 210,
        "trust_score": 0.6,
        "historical_load_5min": 85,
        "last_updated": 1735689600
      }
    ]
  }'
```

`last_updated` is a Unix epoch timestamp (seconds). Use a current
timestamp (e.g. `python3 -c "import time; print(time.time())"`) when
testing, or stats will read as stale.

### Example response

```json
{
  "request_id": "demo-1",
  "recommended_node": "regA-c1-edge2",
  "score": 0.8365,
  "reasoning": "regA-c1-edge2 selected (score=0.837): strongest on low latency + high trust.",
  "all_scores": [
    {
      "node_id": "regA-c1-edge2",
      "score": 0.8365,
      "disqualified": false,
      "disqualify_reason": null,
      "components": {
        "cpu_headroom": 0.8,
        "memory_headroom": 0.7,
        "latency": 0.97,
        "trust": 0.95,
        "historical_load": 0.75
      }
    },
    {
      "node_id": "regA-c1-edge3",
      "score": 0.3285,
      "disqualified": false,
      "disqualify_reason": null,
      "components": { "...": "..." }
    }
  ]
}
```

If every candidate is disqualified (stale, unavailable, or the list is
empty), `recommended_node` and `score` are `null` and `reasoning` explains
why.

---

## Scoring approach

Each valid (fresh + available) candidate gets a weighted score in `[0, 1]`:

| Factor              | Weight |
|----------------------|--------|
| CPU headroom          | 25%   |
| Memory headroom        | 20%   |
| Latency                 | 20%   |
| Trust score              | 20%   |
| 5-min historical load     | 15%   |

Raw metrics are normalized to a 0–1 "goodness" scale before weighting
(e.g. CPU headroom = `(100 - cpu_usage_percent) / 100`; latency is
normalized against a 500ms reference ceiling). Weights and the latency
ceiling are tunable constants at the top of `scorer.py`.

**Disqualification (before scoring):**
- `available: false` → excluded, reason `"unavailable"`
- Stats older than `max_staleness_seconds` (default 30s, overridable per
  request) → excluded, reason `"stale (Ns old)"`
- If *no* candidates survive, the response has `recommended_node: null`
  with an explanatory `reasoning`.

**Ties:** broken deterministically by `node_id` (ascending), so repeated
calls with identical input always return the same winner.

**Reasoning:** generated from each winning candidate's top two scoring
factors (e.g. "strongest on low latency + high trust"), plus a note on
any excluded candidates.

---

## Testing without real nodes

```bash
python3 generator.py     # writes scenarios.json (16 synthetic scenarios)
python3 evaluator.py     # runs advisor vs. a naive baseline, writes evaluation_results.json
```

The baseline always picks the candidate with the lowest CPU usage, with no
other checks. The evaluator prints a side-by-side comparison table and an
accuracy summary against each scenario's expected winner — this is the
Review 2 evaluation evidence. Scenarios cover: clear winners, low-CPU/low-
trust traps, high-trust/high-latency traps, stale candidates, ties, all-
stale / all-unavailable (no valid candidate), mixed disqualifications,
memory-vs-CPU pressure, thrashing history, multi-way races, "least bad
option" under universal pressure, zero-trust avoidance, a single-candidate
edge case, an empty-list edge case, and a staleness-boundary edge case.

---

## Files

| File | Purpose |
|------|---------|
| `models.py` | Pydantic request/response/candidate schemas + validation |
| `scorer.py` | Core weighted scoring logic (the main logic of this service) |
| `app.py` | FastAPI app: `GET /health`, `POST /recommend` |
| `generator.py` | Synthetic scenario generator for testing |
| `evaluator.py` | Advisor-vs-baseline evaluation harness |
| `requirements.txt` | Python dependencies |

## What this service does NOT do

- Does not modify `/node-agent` (Member 1's work).
- Does not implement the Trust Service (Member 3's work) — `trust_score`
  arrives pre-computed inside each candidate's stats.
- Does not perform the actual migration — it only recommends a target.
