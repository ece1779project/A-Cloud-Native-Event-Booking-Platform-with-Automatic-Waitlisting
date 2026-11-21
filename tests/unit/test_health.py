import uuid

from fastapi.testclient import TestClient

from app.core.metrics import HTTP_REQUESTS
from app.main import app


def test_live_returns_ok():
    client = TestClient(app)
    response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_api_metrics_are_exposed():
    client = TestClient(app)
    response = client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "python_info" in response.text


def test_request_id_and_http_metrics_are_added():
    client = TestClient(app)
    before = HTTP_REQUESTS.labels("GET", "/health/live", "200")._value.get()
    response = client.get("/health/live", headers={"X-Request-ID": "safe-client.123"})
    after = HTTP_REQUESTS.labels("GET", "/health/live", "200")._value.get()

    assert response.headers["X-Request-ID"] == "safe-client.123"
    assert after == before + 1


def test_unsafe_request_id_is_replaced():
    response = TestClient(app).get(
        "/health/live", headers={"X-Request-ID": "unsafe value"}
    )
    assert uuid.UUID(response.headers["X-Request-ID"])
