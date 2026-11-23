import json

from app.main import create_app

COMMANDS = [
    ("post", "/api/v1/sessions/{session_id}/book"),
    ("post", "/api/v1/sessions/{session_id}/waitlist"),
    ("post", "/api/v1/participations/{participation_id}/confirm"),
    ("post", "/api/v1/participations/{participation_id}/cancel"),
    ("post", "/api/v1/participations/{participation_id}/decline"),
    ("post", "/api/v1/participations/{participation_id}/leave"),
    ("post", "/api/v1/admin/participations/{participation_id}/cancel"),
    # Management commands (TECHNICAL_DESIGN 7.5, 8.1).
    ("post", "/api/v1/events"),
    ("patch", "/api/v1/events/{event_id}"),
    ("post", "/api/v1/events/{event_id}/publish"),
    ("post", "/api/v1/events/{event_id}/cancel"),
    ("post", "/api/v1/events/{event_id}/sessions"),
    ("patch", "/api/v1/sessions/{session_id}"),
    ("post", "/api/v1/sessions/{session_id}/cancel"),
    ("patch", "/api/v1/admin/accounts/{account_id}"),
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


def test_export_openapi_is_deterministic_json(capsys):
    from app.cli.export_openapi import main

    main()
    first = capsys.readouterr().out
    main()
    assert capsys.readouterr().out == first
    assert json.loads(first)["paths"]["/api/v1/events"]["post"]
