"""Worker health semantics from TECHNICAL_DESIGN 13.3."""

from fastapi.testclient import TestClient

from app.workers.health import create_health_app


class FakeConnection:
    async def execute(self, _statement):
        return None


class ConnectionContext:
    def __init__(self, error: Exception | None = None):
        self.error = error

    async def __aenter__(self):
        if self.error is not None:
            raise self.error
        return FakeConnection()

    async def __aexit__(self, *_args):
        return None


class FakeEngine:
    def __init__(self, error: Exception | None = None):
        self.error = error

    def connect(self):
        return ConnectionContext(self.error)


def test_worker_liveness_does_not_check_dependencies():
    client = TestClient(create_health_app(FakeEngine(RuntimeError("database down"))))
    assert client.get("/health/live").json() == {"status": "ok"}


def test_worker_readiness_checks_only_database():
    client = TestClient(create_health_app(FakeEngine()))
    response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


def test_worker_readiness_returns_503_on_database_failure():
    client = TestClient(create_health_app(FakeEngine(ConnectionError("down"))))
    response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json() == {
        "status": "unavailable",
        "database": "error: ConnectionError",
    }


def test_worker_metrics_are_exposed():
    client = TestClient(create_health_app(FakeEngine()))
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "python_info" in response.text
