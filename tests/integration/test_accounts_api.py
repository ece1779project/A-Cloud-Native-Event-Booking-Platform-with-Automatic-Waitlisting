async def test_register_creates_attendee(client):
    r = await client.post(
        "/api/v1/accounts",
        json={
            "email": "  Alice@Example.com ",
            "display_name": "Alice",
            "password": "x" * 12,
        },
    )
    assert r.status_code == 201, r.text
    data = r.json()["data"]
    assert data["role"] == "ATTENDEE"
    assert data["email"] == "alice@example.com"
    assert "password" not in str(r.json()) and "password_hash" not in data


async def test_register_rejects_role_field(client):
    r = await client.post(
        "/api/v1/accounts",
        json={
            "email": "b@example.com",
            "display_name": "B",
            "password": "x" * 12,
            "role": "ADMIN",
        },
    )
    assert r.status_code == 422


async def test_register_rejects_short_password_and_blank_name(client):
    r = await client.post(
        "/api/v1/accounts",
        json={"email": "c@example.com", "display_name": "   ", "password": "short"},
    )
    assert r.status_code == 422


async def test_duplicate_email_case_insensitive(client):
    body = {"email": "d@example.com", "display_name": "D", "password": "x" * 12}
    assert (await client.post("/api/v1/accounts", json=body)).status_code == 201
    body["email"] = "D@EXAMPLE.COM"
    r = await client.post("/api/v1/accounts", json=body)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "EMAIL_ALREADY_REGISTERED"


async def test_me_requires_token(client):
    r = await client.get("/api/v1/me")
    assert (
        r.status_code == 401 and r.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"
    )


async def test_me_rejects_garbage_token(client):
    r = await client.get("/api/v1/me", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401
