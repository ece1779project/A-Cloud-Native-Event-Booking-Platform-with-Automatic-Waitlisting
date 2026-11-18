from app.main import create_app

COMMANDS = [
    ("post", "/api/v1/sessions/{session_id}/book"),
    ("post", "/api/v1/sessions/{session_id}/waitlist"),
    ("post", "/api/v1/participations/{participation_id}/confirm"),
    ("post", "/api/v1/participations/{participation_id}/cancel"),
]


def test_business_commands_require_idempotency_key():
    spec = create_app().openapi()
    for method, path in COMMANDS:
        params = spec["paths"][path][method].get("parameters", [])
        key = [p for p in params if p["name"].lower() == "idempotency-key"]
        assert key and key[0]["in"] == "header", f"{method} {path}"


def test_public_registration_has_no_role_field():
    spec = create_app().openapi()
    schema = spec["components"]["schemas"]["AccountCreate"]
    assert "role" not in schema["properties"]
    assert schema.get("additionalProperties") is False
