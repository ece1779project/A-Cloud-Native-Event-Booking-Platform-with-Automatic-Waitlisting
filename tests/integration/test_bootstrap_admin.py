from sqlalchemy import select

from app.cli.bootstrap_admin import bootstrap
from app.models.account import Account


async def test_bootstrap_creates_admin_once(session_factory, client):
    assert await bootstrap(
        session_factory, email=" Root@Example.com ", password="a-long-local-password"
    )
    assert not await bootstrap(
        session_factory, email="root@example.com", password="different-password!"
    )
    async with session_factory() as db:
        rows = list(await db.scalars(select(Account)))
    assert [(a.email, a.role) for a in rows] == [("root@example.com", "ADMIN")]
    r = await client.post(
        "/api/v1/auth/login",
        json={"email": "root@example.com", "password": "a-long-local-password"},
    )
    assert r.status_code == 200 and r.json()["data"]["account"]["role"] == "ADMIN"


async def test_main_rejects_missing_or_short_password(monkeypatch, capsys):
    from app.cli import bootstrap_admin
    from app.core.config import get_settings

    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://unused/unused_test")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_EMAIL", "root@example.com")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_PASSWORD", "short")
    get_settings.cache_clear()
    try:
        assert await bootstrap_admin.main() == 2
    finally:
        get_settings.cache_clear()
    assert "short" not in capsys.readouterr().err
