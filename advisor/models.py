"""
models.py — Data validation for the CHAMELEON AI Advisor.
"""

from __future__ import annotations

from typing import Dict, List
from pydantic import BaseModel, Field, field_validator


class CandidateStats(BaseModel):
    """Stats for a single candidate node, as reported by the Node Agent."""

    node_id: str = Field(
        ...,
        min_length=1,
        description="Unique node identifier, e.g. 'regA-c1-edge2'"
    )

    timestamp: str = Field(
        ...,
        description="ISO 8601 UTC timestamp"
    )

    cpu_percent: float = Field(..., ge=0, le=100)
    mem_percent: float = Field(..., ge=0, le=100)

    latency_ms: Dict[str, float] = Field(
        default_factory=dict,
        description="Map of neighbor node_id -> latency in ms"
    )

    history_load_avg_5m: float = Field(..., ge=0, le=100)
    trust_score: float = Field(..., ge=0, le=1)

    @field_validator("node_id")
    @classmethod
    def node_id_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("node_id must not be blank")
        return v


class RecommendRequest(BaseModel):
    """Incoming request from Member 1's Node Agent."""

    overloaded_node: str
    candidates: List[CandidateStats] = Field(..., min_length=1)

    @field_validator("candidates")
    @classmethod
    def unique_node_ids(cls, v: List[CandidateStats]) -> List[CandidateStats]:
        seen = set()
        for c in v:
            if c.node_id in seen:
                raise ValueError(f"duplicate node_id in candidates: {c.node_id}")
            seen.add(c.node_id)
        return v


class CandidateScore(BaseModel):
    """Internal per-candidate score breakdown."""

    node_id: str
    score: float | None = None
    disqualified: bool = False
    disqualify_reason: str | None = None
    components: dict | None = None


class RecommendResponse(BaseModel):
    """Response returned to Member 1."""

    recommended_node: str
    score: float = Field(..., ge=0, le=1)
    reasoning: str


class HealthResponse(BaseModel):
    status: str = "ok"
    service: str = "chameleon-ai-advisor"