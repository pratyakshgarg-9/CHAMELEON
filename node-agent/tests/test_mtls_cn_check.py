import pytest

from app import deps
from app.config import settings


@pytest.fixture
def mtls_on(monkeypatch):
    monkeypatch.setattr(settings, "MTLS_ENABLED", True)


def _register_body(node_id="regA-c1-edge7"):
    return {"node_id": node_id, "region": "regA", "cluster": "c1", "address": "http://localhost:8007"}


def test_register_with_matching_cn_succeeds(client, mtls_on):
    resp = client.post(
        "/register", json=_register_body("regA-c1-edge7"), headers={"X-SSL-Client-CN": "regA-c1-edge7"}
    )
    assert resp.status_code == 200


def test_register_with_mismatched_cn_is_rejected(client, mtls_on):
    resp = client.post(
        "/register", json=_register_body("regA-c1-edge7"), headers={"X-SSL-Client-CN": "regA-c1-edge8"}
    )
    assert resp.status_code == 403


def test_register_with_no_cn_header_is_rejected(client, mtls_on):
    resp = client.post("/register", json=_register_body("regA-c1-edge7"))
    assert resp.status_code == 403


def test_register_unaffected_when_mtls_disabled(client):
    resp = client.post(
        "/register", json=_register_body("regA-c1-edge7"), headers={"X-SSL-Client-CN": "someone-else"}
    )
    assert resp.status_code == 200


def test_heartbeat_with_mismatched_cn_is_rejected(client, mtls_on):
    resp = client.post(
        "/heartbeat", json={"from_node_id": "regA-c1-edge7"}, headers={"X-SSL-Client-CN": "regA-c1-edge8"}
    )
    assert resp.status_code == 403


def test_heartbeat_with_no_claimed_identity_is_unaffected(client, mtls_on):
    # from_node_id is optional — a heartbeat that doesn't claim an
    # identity has nothing to check against, so it's not rejected.
    resp = client.post("/heartbeat", json={}, headers={"X-SSL-Client-CN": "regA-c1-edge7"})
    assert resp.status_code == 200


def test_coordinator_with_mismatched_leader_cn_is_rejected(client, mtls_on):
    resp = client.post(
        "/coordinator", json={"leader_node_id": "regA-c1-edge9"}, headers={"X-SSL-Client-CN": "regA-c1-edge8"}
    )
    assert resp.status_code == 403


def test_cn_mismatch_auto_reports_the_impersonator_to_trust_service(client, mtls_on, monkeypatch):
    reports = []

    async def fake_post_json(url, body):
        reports.append((url, body))
        return {"status": "ok"}

    monkeypatch.setattr(deps, "post_json", fake_post_json)

    resp = client.post(
        "/register", json=_register_body("regA-c1-edge7"), headers={"X-SSL-Client-CN": "attacker"}
    )
    assert resp.status_code == 403

    assert len(reports) == 1
    url, body = reports[0]
    assert url == f"{settings.TRUST_URL}/trust/report"
    # Reports the cert's REAL identity (the impersonator), not the false claim.
    assert body == {"node_id": "attacker", "event_type": "auth_failure"}
