import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient

import app as trust_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Isolate each test's SQLite state — the module-level trust_scores
    # dict and DB_PATH are both process-global, so tests would otherwise
    # bleed into each other and into the real trust.db on disk.
    monkeypatch.setattr(trust_app, "DB_PATH", str(tmp_path / "test_trust.db"))
    trust_app.init_db()
    trust_app.trust_scores.clear()
    return TestClient(trust_app.app)


def test_auto_isolates_after_repeated_auth_failure(client):
    node_id = "regA-c1-attacker"
    for _ in range(3):
        resp = client.post("/trust/report", json={"node_id": node_id, "event_type": "auth_failure"})
        assert resp.status_code == 200

    score = client.get(f"/trust/score/{node_id}").json()
    assert score["trust_score"] == 0.0
    assert "isolated" in score["flags"]


def test_does_not_isolate_before_score_bottoms_out(client):
    node_id = "regA-c1-edge2"
    resp = client.post("/trust/report", json={"node_id": node_id, "event_type": "auth_failure"})
    score = resp.json()
    assert score["trust_score"] > 0.0
    assert "isolated" not in score["flags"]


def test_score_query_is_not_restricted_to_self(client):
    # Deliberately unrestricted (see app.py's TrustReport comment) — any
    # mesh member needs to check ANY other node's trust score to make
    # election/migration decisions about it.
    resp = client.get("/trust/score/regA-c1-edge3")
    assert resp.status_code == 200


def test_clean_migration_increases_trust_but_never_isolates(client):
    node_id = "regA-c1-edge2"
    resp = client.post("/trust/report", json={"node_id": node_id, "event_type": "clean_migration"})
    score = resp.json()
    assert score["trust_score"] > 0.5
    assert "isolated" not in score["flags"]
