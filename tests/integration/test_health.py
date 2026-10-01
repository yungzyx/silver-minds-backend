from fastapi.testclient import TestClient


def test_health_reports_process_alive(client: TestClient) -> None:
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_readiness_reports_database_pgvector_and_simulated_providers(client: TestClient) -> None:
    response = client.get("/api/v1/health/ready")

    body = response.json()
    assert response.status_code == 200
    assert body["database"] is True
    assert body["pgvector"] is True
    assert body["ai_provider"] == "fake"
    assert body["email_provider"] == "fake"


def test_unknown_route_uses_error_envelope(client: TestClient) -> None:
    response = client.get("/api/v1/no-existe")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_openapi_contract_is_served(client: TestClient) -> None:
    response = client.get("/openapi.json")

    assert response.status_code == 200
    assert "/api/v1/health" in response.json()["paths"]
