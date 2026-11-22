import pytest

from app.domain.enums import Role
from tests.integration.conftest import idem, published_session


@pytest.fixture
async def world(client, make_user):
    a1_acct, a1 = await make_user(Role.ATTENDEE)
    _, a2 = await make_user(Role.ATTENDEE)
    _, o1 = await make_user(Role.ORGANIZER)
    _, o2 = await make_user(Role.ORGANIZER)
    _, admin = await make_user(Role.ADMIN)
    event_id, session_id = await published_session(client, o1, capacity=5)
    draft = (await client.post("/api/v1/events", headers=o1,
                               json={"title": "Draft", "description": "d", "location": "l"})).json()["data"]
    p = (await client.post(f"/api/v1/sessions/{session_id}/book", headers=a1 | idem())).json()["data"]
    return {
        "headers": {"anon": {}, "a1": a1, "a2": a2, "o1": o1, "o2": o2, "admin": admin},
        "event": event_id, "session": session_id, "draft": draft["id"],
        "participation": p["id"], "a1_id": str(a1_acct.id),
    }


ACTORS = ("anon", "a1", "a2", "o1", "o2", "admin")

CASES = [
    ("list events", "GET", "/api/v1/events", None, False,
     dict(anon=200, a1=200, a2=200, o1=200, o2=200, admin=200)),
    ("view draft", "GET", "/api/v1/events/{draft}", None, False,
     dict(anon=404, a1=404, a2=404, o1=200, o2=404, admin=200)),
    ("create event", "POST", "/api/v1/events", {"title": "t", "description": "d", "location": "l"}, False,
     dict(anon=401, a1=403, a2=403, o1=201, o2=201, admin=201)),
    ("edit event", "PATCH", "/api/v1/events/{event}", {"location": "x"}, False,
     dict(anon=401, a1=403, a2=403, o1=200, o2=403, admin=200)),
    ("roster", "GET", "/api/v1/sessions/{session}/roster", None, False,
     dict(anon=401, a1=403, a2=403, o1=200, o2=403, admin=200)),
    ("read participation", "GET", "/api/v1/participations/{participation}", None, False,
     dict(anon=401, a1=200, a2=404, o1=404, o2=404, admin=404)),
    ("confirm someone else's", "POST", "/api/v1/participations/{participation}/confirm",
     {"simulation_result": "failure"}, True,
     dict(anon=401, a1=200, a2=404, o1=404, o2=404, admin=404)),
    # a1 owns the attempt, but it is HELD, so decline and leave are 409.
    ("decline someone else's", "POST", "/api/v1/participations/{participation}/decline", None, True,
     dict(anon=401, a1=409, a2=404, o1=404, o2=404, admin=404)),
    ("leave someone else's", "POST", "/api/v1/participations/{participation}/leave", None, True,
     dict(anon=401, a1=409, a2=404, o1=404, o2=404, admin=404)),
    ("admin list accounts", "GET", "/api/v1/admin/accounts", None, False,
     dict(anon=401, a1=403, a2=403, o1=403, o2=403, admin=200)),
]


@pytest.mark.parametrize("name,method,path,body,needs_key,expected", CASES, ids=[c[0] for c in CASES])
@pytest.mark.parametrize("actor", ACTORS)
async def test_matrix(client, world, actor, name, method, path, body, needs_key, expected):
    headers = dict(world["headers"][actor])
    if needs_key:
        headers |= idem()
    url = path.format(**world)
    r = await client.request(method, url, headers=headers, json=body)
    assert r.status_code == expected[actor], f"{actor} {name}: {r.status_code} {r.text}"
