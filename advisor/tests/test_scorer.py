import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from models import CandidateStats
from scorer import score_candidates


def _candidate(node_id, trust_score, cpu=10.0, mem=10.0, latency=5.0):
    return CandidateStats(
        node_id=node_id,
        timestamp=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        cpu_percent=cpu,
        mem_percent=mem,
        latency_ms={"regA-c1-edge1": latency},
        history_load_avg_5m=cpu,
        trust_score=trust_score,
    )


def test_isolated_candidate_is_disqualified_even_when_otherwise_best():
    # regA-c1-attacker is strictly better on every scored metric except
    # trust — must still lose outright, not just take a 20%-weight hit,
    # or "isolates suspicious nodes" isn't actually true.
    result = score_candidates(
        candidates=[
            _candidate("regA-c1-attacker", trust_score=0.0, cpu=5, mem=5, latency=1),
            _candidate("regA-c1-edge3", trust_score=0.9, cpu=40, mem=40, latency=30),
        ],
        overloaded_node="regA-c1-edge1",
    )
    assert result.winner.node_id == "regA-c1-edge3"
    disqualified = {s.node_id: s.disqualify_reason for s in result.all_scores if s.disqualified}
    assert disqualified["regA-c1-attacker"] == "isolated (trust_score=0)"


def test_low_but_nonzero_trust_is_only_down_weighted_not_disqualified():
    # A candidate with SOME trust (not isolated) should still be eligible
    # — only trust_score == 0 (actually isolated) is a hard cutoff.
    result = score_candidates(
        candidates=[_candidate("regA-c1-edge2", trust_score=0.1)],
        overloaded_node="regA-c1-edge1",
    )
    assert result.winner is not None
    assert result.winner.node_id == "regA-c1-edge2"
