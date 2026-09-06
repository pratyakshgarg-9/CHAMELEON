"""
scorer.py — Main recommendation scoring logic.

Weights:
  CPU headroom        25%
  Memory headroom      20%
  Latency               20%
  Trust                 20%
  Historical load       15%
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional

from models import CandidateStats


WEIGHTS = {
    "cpu_headroom": 0.25,
    "memory_headroom": 0.20,
    "latency": 0.20,
    "trust": 0.20,
    "historical_load": 0.15,
}

LATENCY_CEILING_MS = 500.0
SCORE_TIE_EPSILON = 1e-6


@dataclass
class ScoredCandidate:
    node_id: str
    score: Optional[float]
    disqualified: bool = False
    disqualify_reason: Optional[str] = None
    components: dict = field(default_factory=dict)


@dataclass
class ScoringResult:
    winner: Optional[ScoredCandidate]
    all_scores: List[ScoredCandidate]
    reasoning: str


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _is_stale(
    candidate: CandidateStats,
    max_staleness_seconds: float,
) -> tuple[bool, float]:

    timestamp = candidate.timestamp

    if timestamp.endswith("Z"):
        timestamp = timestamp[:-1] + "+00:00"

    candidate_time = datetime.fromisoformat(timestamp)

    if candidate_time.tzinfo is None:
        candidate_time = candidate_time.replace(tzinfo=timezone.utc)

    now = datetime.now(timezone.utc)
    age = (now - candidate_time).total_seconds()

    return age > max_staleness_seconds, age


def _score_one(
    candidate: CandidateStats,
    overloaded_node: str,
) -> ScoredCandidate:

    cpu_headroom = _clamp01((100.0 - candidate.cpu_percent) / 100.0)

    memory_headroom = _clamp01(
        (100.0 - candidate.mem_percent) / 100.0
    )

    # Candidate's latency map should contain the overloaded node.
    latency_ms = candidate.latency_ms.get(overloaded_node)

    if latency_ms is None:
        return ScoredCandidate(
            node_id=candidate.node_id,
            score=None,
            disqualified=True,
            disqualify_reason="latency unavailable",
        )

    latency_component = _clamp01(
        1.0 - (latency_ms / LATENCY_CEILING_MS)
    )

    trust_component = _clamp01(candidate.trust_score)

    historical_load_component = _clamp01(
        (100.0 - candidate.history_load_avg_5m) / 100.0
    )

    components = {
        "cpu_headroom": cpu_headroom,
        "memory_headroom": memory_headroom,
        "latency": latency_component,
        "trust": trust_component,
        "historical_load": historical_load_component,
    }

    final_score = (
        WEIGHTS["cpu_headroom"] * cpu_headroom
        + WEIGHTS["memory_headroom"] * memory_headroom
        + WEIGHTS["latency"] * latency_component
        + WEIGHTS["trust"] * trust_component
        + WEIGHTS["historical_load"] * historical_load_component
    )

    return ScoredCandidate(
        node_id=candidate.node_id,
        score=round(final_score, 6),
        components=components,
    )


def _build_reasoning(
    winner: ScoredCandidate,
    disqualified: List[ScoredCandidate],
) -> str:

    c = winner.components

    ranked = sorted(
        c.items(),
        key=lambda kv: kv[1],
        reverse=True
    )

    top_factors = ranked[:2]

    factor_names = {
        "cpu_headroom": "high CPU headroom",
        "memory_headroom": "high memory headroom",
        "latency": "low latency",
        "trust": "high trust",
        "historical_load": "healthy recent load",
    }

    strengths = " + ".join(
        factor_names[name] for name, _ in top_factors
    )

    reasoning = (
        f"{winner.node_id} selected: strongest on {strengths}."
    )

    if disqualified:
        names = ", ".join(
            f"{d.node_id} ({d.disqualify_reason})"
            for d in disqualified
        )
        reasoning += f" Excluded: {names}."

    return reasoning


def score_candidates(
    candidates: List[CandidateStats],
    overloaded_node: str,
    max_staleness_seconds: float = 30.0,
) -> ScoringResult:

    all_scores: List[ScoredCandidate] = []
    valid: List[ScoredCandidate] = []
    disqualified: List[ScoredCandidate] = []

    for candidate in candidates:

        stale, age = _is_stale(
            candidate,
            max_staleness_seconds
        )

        if stale:
            sc = ScoredCandidate(
                node_id=candidate.node_id,
                score=None,
                disqualified=True,
                disqualify_reason=f"stale ({age:.1f}s old)",
            )

            all_scores.append(sc)
            disqualified.append(sc)
            continue

        scored = _score_one(
            candidate,
            overloaded_node
        )

        all_scores.append(scored)

        if scored.disqualified:
            disqualified.append(scored)
        else:
            valid.append(scored)

    if not valid:
        return ScoringResult(
            winner=None,
            all_scores=all_scores,
            reasoning="No suitable candidate available.",
        )

    # Highest score wins.
    # If scores tie, lowest node_id wins.
    valid_sorted = sorted(
        valid,
        key=lambda s: (-s.score, s.node_id)
    )

    winner = valid_sorted[0]

    tied = [
        s for s in valid_sorted
        if abs(s.score - winner.score) < SCORE_TIE_EPSILON
    ]

    reasoning = _build_reasoning(
        winner,
        disqualified
    )

    if len(tied) > 1:
        tied_names = ", ".join(
            s.node_id for s in tied
        )

        reasoning += (
            f" Tie among [{tied_names}], "
            "resolved alphabetically by node_id."
        )

    return ScoringResult(
        winner=winner,
        all_scores=all_scores,
        reasoning=reasoning,
    )