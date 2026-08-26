def test_register_then_appears_in_neighbors(client):
    body = {
        "node_id": "regA-c1-edge9",
        "region": "regA",
        "cluster": "c1",
        "address": "http://localhost:8009",
    }
    resp = client.post("/register", json=body)
    assert resp.status_code == 200
    assert resp.json()["status"] == "registered"

    neighbors = client.get("/neighbors").json()["neighbors"]
    ids = [n["node_id"] for n in neighbors]
    assert "regA-c1-edge9" in ids


def test_heartbeat_returns_alive(client):
    resp = client.post("/heartbeat", json={})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "alive"
    assert "timestamp" in body


def test_heartbeat_updates_last_seen(client):
    client.post(
        "/register",
        json={
            "node_id": "regA-c1-edge5",
            "region": "regA",
            "cluster": "c1",
            "address": "http://localhost:8005",
        },
    )
    resp = client.post("/heartbeat", json={"from_node_id": "regA-c1-edge5"})
    assert resp.status_code == 200

    neighbors = client.get("/neighbors").json()["neighbors"]
    peer = next(n for n in neighbors if n["node_id"] == "regA-c1-edge5")
    assert peer["last_seen"] is not None
