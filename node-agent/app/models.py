from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class StatsPayload(BaseModel):
    """Mirrors /shared/schemas/stats_payload.schema.json field-for-field."""

    model_config = ConfigDict(extra="forbid")

    node_id: str
    timestamp: str
    cpu_percent: float = Field(ge=0, le=100)
    mem_percent: float = Field(ge=0, le=100)
    latency_ms: dict[str, float] = Field(default_factory=dict)
    history_load_avg_5m: float = Field(ge=0, le=100)
    trust_score: float = Field(ge=0, le=1)


class RecommendRequest(BaseModel):
    """Mirrors /shared/schemas/recommend_request.schema.json."""

    model_config = ConfigDict(extra="forbid")

    overloaded_node: str
    candidates: list[StatsPayload] = Field(min_length=1)


class RecommendResponse(BaseModel):
    """Mirrors /shared/schemas/recommend_response.schema.json."""

    model_config = ConfigDict(extra="forbid")

    recommended_node: str
    score: float = Field(ge=0, le=1)
    reasoning: str


class TrustScoreResponse(BaseModel):
    """Mirrors /shared/schemas/trust_score_response.schema.json."""

    model_config = ConfigDict(extra="forbid")

    node_id: str
    trust_score: float = Field(ge=0, le=1)
    last_updated: str
    flags: list[str] = Field(default_factory=list)


class RegisterRequest(BaseModel):
    """Peer bootstrap announce — node-agent-local, not a /shared contract."""

    node_id: str
    region: str
    cluster: str
    address: str


class RegisterResponse(BaseModel):
    status: str
    node_id: str


class HeartbeatRequest(BaseModel):
    from_node_id: Optional[str] = None


class HeartbeatResponse(BaseModel):
    node_id: str
    timestamp: str
    status: str


class MigrateOutRequest(BaseModel):
    container_name: str
    destination_node: str


class MigrateOutResponse(BaseModel):
    status: str
    container_name: str
    destination_node: Optional[str] = None
    detail: Optional[str] = None


class MigrateInResponse(BaseModel):
    status: str
    container_name: str
    node_id: str
