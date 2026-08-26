import json
from pathlib import Path

import jsonschema

SCHEMA_PATH = (
    Path(__file__).resolve().parents[2] / "shared" / "schemas" / "stats_payload.schema.json"
)


def test_stats_matches_shared_schema(client):
    resp = client.get("/stats")
    assert resp.status_code == 200
    body = resp.json()

    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    jsonschema.validate(instance=body, schema=schema)


def test_stats_fields_in_range(client):
    body = client.get("/stats").json()
    assert 0 <= body["cpu_percent"] <= 100
    assert 0 <= body["mem_percent"] <= 100
    assert 0 <= body["history_load_avg_5m"] <= 100
    assert 0 <= body["trust_score"] <= 1


def test_stats_reflects_measured_latency(client):
    registry = client.app.state.registry
    registry.mark_registered("regA-c1-edge2", "http://localhost:9999", "regA", "c1")
    registry.update_latency("regA-c1-edge2", 12.5)

    body = client.get("/stats").json()

    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    jsonschema.validate(instance=body, schema=schema)
    assert body["latency_ms"]["regA-c1-edge2"] == 12.5
