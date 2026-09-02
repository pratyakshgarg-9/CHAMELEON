"""
evaluator.py — Prove the advisor's approach beats a naive baseline.

Runs every generated scenario through:
  1. Your Advisor  (scorer.score_candidates)
  2. Naive Baseline (always picks the candidate with the lowest CPU usage,
     ignoring staleness/availability/trust/latency entirely)

...and reports a comparison table plus a match/accuracy summary against
each scenario's expected winner. This is the Review 2 evaluation evidence.

Run with:
  python evaluator.py
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional

from generator import generate_scenarios
from models import CandidateStats
from scorer import score_candidates


def naive_baseline(scenario: Dict[str, Any]) -> Optional[str]:
    """Always select the candidate with the lowest CPU usage (no other checks)."""
    candidates = scenario["candidates"]
    if not candidates:
        return None
    best = min(candidates, key=lambda c: c["cpu_usage_percent"])
    return best["node_id"]


def run_advisor(scenario: Dict[str, Any]) -> Dict[str, Any]:
    candidates = [CandidateStats(**c) for c in scenario["candidates"]]
    result = score_candidates(
        candidates=candidates,
        max_staleness_seconds=scenario.get("max_staleness_seconds", 30),
        now=time.time(),
    )
    winner = result.winner.node_id if result.winner else None
    return {"winner": winner, "reasoning": result.reasoning}


def evaluate() -> List[Dict[str, Any]]:
    scenarios = generate_scenarios()
    rows = []

    for s in scenarios:
        advisor_result = run_advisor(s)
        baseline_winner = naive_baseline(s)
        expected = s["expected"]

        advisor_correct = advisor_result["winner"] == expected
        baseline_correct = baseline_winner == expected

        rows.append({
            "scenario": s["name"],
            "description": s["description"],
            "expected": expected,
            "advisor": advisor_result["winner"],
            "advisor_correct": advisor_correct,
            "baseline": baseline_winner,
            "baseline_correct": baseline_correct,
            "advisor_reasoning": advisor_result["reasoning"],
        })

    return rows


def print_report(rows: List[Dict[str, Any]]) -> None:
    total = len(rows)
    advisor_correct = sum(1 for r in rows if r["advisor_correct"])
    baseline_correct = sum(1 for r in rows if r["baseline_correct"])

    header = f'{"Scenario":<28} | {"Advisor":<16} | {"Baseline":<16} | {"Expected":<16} | Match'
    print(header)
    print("-" * len(header))
    for r in rows:
        adv = r["advisor"] or "(none)"
        base = r["baseline"] or "(none)"
        exp = r["expected"] or "(none)"
        match = "A" if r["advisor_correct"] else ""
        match += "B" if r["baseline_correct"] else ""
        match = match or "-"
        print(f'{r["scenario"]:<28} | {adv:<16} | {base:<16} | {exp:<16} | {match}')

    print()
    print(f"Advisor accuracy:  {advisor_correct}/{total} ({100 * advisor_correct / total:.1f}%)")
    print(f"Baseline accuracy: {baseline_correct}/{total} ({100 * baseline_correct / total:.1f}%)")
    print()
    print("Reasoning examples (advisor):")
    for r in rows[:5]:
        print(f'  - [{r["scenario"]}] {r["advisor_reasoning"]}')


if __name__ == "__main__":
    rows = evaluate()
    print_report(rows)
    with open("evaluation_results.json", "w") as f:
        json.dump(rows, f, indent=2)
    print("\nFull results saved to evaluation_results.json")
