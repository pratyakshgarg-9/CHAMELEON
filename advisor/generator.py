"""
generator.py — Synthetic scenario generator.

Produces realistic (and deliberately tricky) node-stat combinations so the
advisor can be tested and evaluated without needing real AWS nodes.

Each scenario is a dict:
{
  "name": str,
  "description": str,
  "candidates": [CandidateStats-shaped dicts],
  "expected": str | None,     # expected winning node_id, or None if the
                               # scenario expects "no valid candidate"
  "max_staleness_seconds": float,
}

Run directly to print/save 10-20+ scenarios as JSON:
  python generator.py
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional


def _candidate(
    node_id: str,
    cpu: float,
    mem: float,
    latency: float,
    trust: float,
    hist_load: float,
    age_seconds: float = 1.0,
    available: bool = True,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    now = now if now is not None else time.time()
    return {
        "node_id": node_id,
        "cpu_usage_percent": cpu,
        "memory_usage_percent": mem,
        "latency_ms": latency,
        "trust_score": trust,
        "historical_load_5min": hist_load,
        "last_updated": now - age_seconds,
        "available": available,
    }


def generate_scenarios() -> List[Dict[str, Any]]:
    now = time.time()
    scenarios: List[Dict[str, Any]] = []

    # 1. Clear-cut winner: low CPU, low latency, high trust vs a loaded, laggy node.
    scenarios.append({
        "name": "clear_winner",
        "description": "edge2 is lightly loaded and fast; edge3 is overloaded and slow.",
        "candidates": [
            _candidate("regA-c1-edge2", cpu=20, mem=30, latency=15, trust=0.95, hist_load=25, now=now),
            _candidate("regA-c1-edge3", cpu=88, mem=80, latency=210, trust=0.6, hist_load=85, now=now),
        ],
        "expected": "regA-c1-edge2",
        "max_staleness_seconds": 30,
    })

    # 2. Low CPU but poor trust vs moderate CPU with strong trust.
    scenarios.append({
        "name": "low_cpu_poor_trust",
        "description": "edge4 has great CPU headroom but near-zero trust; edge5 is decent everywhere with full trust.",
        "candidates": [
            _candidate("edge4", cpu=15, mem=20, latency=45, trust=0.05, hist_load=25, now=now),
            _candidate("edge5", cpu=40, mem=35, latency=50, trust=1.0, hist_load=35, now=now),
        ],
        "expected": "edge5",
        "max_staleness_seconds": 30,
    })

    # 3. High trust but high latency vs balanced candidate.
    scenarios.append({
        "name": "high_trust_high_latency",
        "description": "edge6 is fully trusted but far away (latency); edge7 is closer and still trustworthy.",
        "candidates": [
            _candidate("edge6", cpu=35, mem=35, latency=480, trust=1.0, hist_load=30, now=now),
            _candidate("edge7", cpu=40, mem=38, latency=60, trust=0.8, hist_load=35, now=now),
        ],
        "expected": "edge7",
        "max_staleness_seconds": 30,
    })

    # 4. Stale candidate should be excluded even though its stats look great.
    scenarios.append({
        "name": "stale_candidate_excluded",
        "description": "edge8 looks perfect but its stats are 5 minutes old; edge9 is decent and fresh.",
        "candidates": [
            _candidate("edge8", cpu=5, mem=5, latency=5, trust=1.0, hist_load=5, age_seconds=300, now=now),
            _candidate("edge9", cpu=50, mem=50, latency=80, trust=0.7, hist_load=50, now=now),
        ],
        "expected": "edge9",
        "max_staleness_seconds": 30,
    })

    # 5. Tied candidates -> deterministic alphabetical tie-break.
    identical_kwargs = dict(cpu=30, mem=30, latency=30, trust=0.8, hist_load=30, now=now)
    scenarios.append({
        "name": "tied_candidates",
        "description": "edge10 and edge11 have identical stats; tie should resolve alphabetically.",
        "candidates": [
            _candidate("edge11", **identical_kwargs),
            _candidate("edge10", **identical_kwargs),
        ],
        "expected": "edge10",
        "max_staleness_seconds": 30,
    })

    # 6. All candidates unsuitable (all stale) -> no winner.
    scenarios.append({
        "name": "all_stale_no_winner",
        "description": "Every candidate's stats are too old to trust.",
        "candidates": [
            _candidate("edge12", cpu=20, mem=20, latency=20, trust=0.9, hist_load=20, age_seconds=120, now=now),
            _candidate("edge13", cpu=25, mem=25, latency=25, trust=0.85, hist_load=25, age_seconds=90, now=now),
        ],
        "expected": None,
        "max_staleness_seconds": 30,
    })

    # 7. All candidates unavailable -> no winner.
    scenarios.append({
        "name": "all_unavailable_no_winner",
        "description": "Both candidates are marked unavailable (e.g. draining, cordoned).",
        "candidates": [
            _candidate("edge14", cpu=10, mem=10, latency=10, trust=0.9, hist_load=10, available=False, now=now),
            _candidate("edge15", cpu=15, mem=15, latency=15, trust=0.9, hist_load=15, available=False, now=now),
        ],
        "expected": None,
        "max_staleness_seconds": 30,
    })

    # 8. Mixed: one stale + one unavailable + one valid -> valid one wins by default.
    scenarios.append({
        "name": "mixed_disqualifications",
        "description": "One stale, one unavailable, one healthy valid candidate.",
        "candidates": [
            _candidate("edge16", cpu=15, mem=15, latency=15, trust=0.95, hist_load=15, age_seconds=200, now=now),
            _candidate("edge17", cpu=20, mem=20, latency=20, trust=0.9, hist_load=20, available=False, now=now),
            _candidate("edge18", cpu=55, mem=55, latency=90, trust=0.7, hist_load=55, now=now),
        ],
        "expected": "edge18",
        "max_staleness_seconds": 30,
    })

    # 9. Memory-constrained candidate loses to CPU-constrained candidate with better memory.
    scenarios.append({
        "name": "memory_vs_cpu_pressure",
        "description": "edge19 is memory-starved; edge20 is CPU-heavy but has memory to spare.",
        "candidates": [
            _candidate("edge19", cpu=30, mem=95, latency=40, trust=0.8, hist_load=40, now=now),
            _candidate("edge20", cpu=60, mem=30, latency=40, trust=0.8, hist_load=40, now=now),
        ],
        "expected": "edge20",
        "max_staleness_seconds": 30,
    })

    # 10. Historical load matters even when instantaneous CPU looks fine (thrashy node).
    scenarios.append({
        "name": "thrashy_history_penalized",
        "description": "edge21 looks okay right now but has been thrashing hard for 5 minutes; edge22 is steady.",
        "candidates": [
            _candidate("edge21", cpu=20, mem=20, latency=20, trust=0.85, hist_load=98, now=now),
            _candidate("edge22", cpu=35, mem=35, latency=20, trust=0.85, hist_load=25, now=now),
        ],
        "expected": "edge22",
        "max_staleness_seconds": 30,
    })

    # 11. Three-way race with a clear best-of-three.
    scenarios.append({
        "name": "three_way_race",
        "description": "Three plausible candidates; edge25 is best across the board.",
        "candidates": [
            _candidate("edge23", cpu=50, mem=50, latency=100, trust=0.6, hist_load=50, now=now),
            _candidate("edge24", cpu=40, mem=60, latency=150, trust=0.75, hist_load=45, now=now),
            _candidate("edge25", cpu=25, mem=25, latency=30, trust=0.92, hist_load=20, now=now),
        ],
        "expected": "edge25",
        "max_staleness_seconds": 30,
    })

    # 12. Extremely overloaded cluster: pick "least bad" option.
    scenarios.append({
        "name": "least_bad_option",
        "description": "Every candidate is under pressure; advisor should still pick the relatively best one.",
        "candidates": [
            _candidate("edge26", cpu=92, mem=88, latency=300, trust=0.5, hist_load=90, now=now),
            _candidate("edge27", cpu=85, mem=90, latency=350, trust=0.55, hist_load=95, now=now),
            _candidate("edge28", cpu=80, mem=82, latency=280, trust=0.6, hist_load=88, now=now),
        ],
        "expected": "edge28",
        "max_staleness_seconds": 30,
    })

    # 13. Zero-trust candidate should almost never win even if resources are great.
    scenarios.append({
        "name": "zero_trust_avoided",
        "description": "edge29 has decent resources but zero trust; edge30 is similar but highly trusted.",
        "candidates": [
            _candidate("edge29", cpu=45, mem=45, latency=45, trust=0.0, hist_load=45, now=now),
            _candidate("edge30", cpu=55, mem=55, latency=60, trust=0.95, hist_load=50, now=now),
        ],
        "expected": "edge30",
        "max_staleness_seconds": 30,
    })

    # 14. Single candidate, trivially wins (sanity check for the single-node edge case).
    scenarios.append({
        "name": "single_candidate",
        "description": "Only one candidate is offered; it should win by default (if valid).",
        "candidates": [
            _candidate("edge31", cpu=70, mem=70, latency=200, trust=0.5, hist_load=70, now=now),
        ],
        "expected": "edge31",
        "max_staleness_seconds": 30,
    })

    # 15. Empty candidate list -> no winner (guards against upstream bugs).
    scenarios.append({
        "name": "empty_candidate_list",
        "description": "No candidates sent at all.",
        "candidates": [],
        "expected": None,
        "max_staleness_seconds": 30,
    })

    # 16. Boundary staleness: exactly at the threshold should still count as stale (> not >=).
    scenarios.append({
        "name": "boundary_staleness",
        "description": "edge32 is just past the staleness threshold; edge33 is comfortably fresh.",
        "candidates": [
            _candidate("edge32", cpu=20, mem=20, latency=20, trust=0.9, hist_load=20, age_seconds=31, now=now),
            _candidate("edge33", cpu=45, mem=45, latency=45, trust=0.75, hist_load=45, now=now),
        ],
        "expected": "edge33",
        "max_staleness_seconds": 30,
    })

    return scenarios


if __name__ == "__main__":
    scenarios = generate_scenarios()
    print(f"Generated {len(scenarios)} scenarios.")
    with open("scenarios.json", "w") as f:
        json.dump(scenarios, f, indent=2)
    print("Saved to scenarios.json")
