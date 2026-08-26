def test_neighbors_empty_by_default(client):
    resp = client.get("/neighbors")
    assert resp.status_code == 200
    assert resp.json() == {"neighbors": []}
