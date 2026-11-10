"""Health endpoints -- the things a load balancer and CI will poll."""


def test_liveness(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_hits_the_database(client):
    response = client.get("/health/db")
    assert response.status_code == 200
    assert response.json()["database"] == "reachable"


def test_root(client):
    assert client.get("/").status_code == 200
