from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.exception_handlers import register_exception_handlers
from app.api.middleware import RequestIdMiddleware
from app.core.errors import AppError, ErrorCode


def _app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)
    register_exception_handlers(app)

    @app.get("/boom")
    async def boom():
        raise AppError(ErrorCode.WAITLIST_REQUIRED, details={"available": 0}, session_version=7)

    @app.get("/lock")
    async def lock():
        raise AppError(ErrorCode.LOCK_TIMEOUT)

    @app.get("/q")
    async def q(n: int):
        return {"n": n}

    return app


def test_app_error_envelope():
    r = TestClient(_app()).get("/boom")
    assert r.status_code == 409
    body = r.json()
    assert body["error"]["code"] == "WAITLIST_REQUIRED"
    assert body["error"]["retryable"] is False
    assert body["meta"]["session_version"] == 7
    assert body["meta"]["server_time"].endswith("Z")
    assert r.headers["X-Request-ID"] == body["meta"]["request_id"]


def test_lock_timeout_is_retryable_503():
    r = TestClient(_app()).get("/lock")
    assert r.status_code == 503 and r.json()["error"]["retryable"] is True


def test_validation_error_is_422_envelope():
    r = TestClient(_app()).get("/q", params={"n": "x"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"


def test_unknown_route_is_404_envelope():
    r = TestClient(_app()).get("/nope")
    assert r.json()["error"]["code"] == "RESOURCE_NOT_FOUND"


def test_safe_request_id_is_kept_and_unsafe_replaced():
    c = TestClient(_app())
    assert c.get("/q?n=1", headers={"X-Request-ID": "abc-123"}).headers["X-Request-ID"] == "abc-123"
    assert c.get("/q?n=1", headers={"X-Request-ID": "bad\nvalue"}).headers["X-Request-ID"] != "bad\nvalue"
