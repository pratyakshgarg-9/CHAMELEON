import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient

import app as coordinator_app


@pytest.fixture
def client():
    return TestClient(coordinator_app.app)


@pytest.fixture
def mtls_on(monkeypatch):
    monkeypatch.setattr(coordinator_app, "MTLS_ENABLED", True)


def test_register_with_matching_leader_cn_succeeds(client, mtls_on):
    resp = client.post(
        "/coordinator/register",
        json={"region": "regA", "cluster": "c1", "leader_node_id": "regA-c1-edge3"},
        headers={"X-SSL-Client-CN": "regA-c1-edge3"},
    )
    assert resp.status_code == 200


def test_register_with_mismatched_leader_cn_is_rejected(client, mtls_on):
    resp = client.post(
        "/coordinator/register",
        json={"region": "regA", "cluster": "c1", "leader_node_id": "regA-c1-edge3"},
        headers={"X-SSL-Client-CN": "attacker"},
    )
    assert resp.status_code == 403


def test_escalate_with_mismatched_node_id_is_rejected(client, mtls_on):
    resp = client.post(
        "/coordinator/escalate",
        json={"node_id": "regA-c1-edge2", "region": "regA", "cluster": "c1", "reason": "test", "candidates_considered": 0},
        headers={"X-SSL-Client-CN": "attacker"},
    )
    assert resp.status_code == 403


def test_unaffected_when_mtls_disabled(client):
    resp = client.post(
        "/coordinator/register",
        json={"region": "regA", "cluster": "c1", "leader_node_id": "regA-c1-edge3"},
        headers={"X-SSL-Client-CN": "attacker"},
    )
    assert resp.status_code == 200
