import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.core import security as sec


def test_password_roundtrip():
    h = sec.hash_password("correct horse battery")
    assert h.startswith("$argon2id$")
    assert sec.verify_password(h, "correct horse battery")
    assert not sec.verify_password(h, "wrong")
    assert not sec.verify_password(None, "dummy-password-for-timing")
    assert not sec.verify_password("garbage", "x")


def _issue(keys, now, ttl=900):
    return sec.issue_access_token(
        account_id=uuid.uuid4(),
        now=now,
        private_key=keys[0],
        issuer="seatflow",
        audience="seatflow-api",
        ttl_seconds=ttl,
    )


def _decode(keys, token, **kw):
    return sec.decode_access_token(
        token,
        public_key=keys[1],
        issuer=kw.get("iss", "seatflow"),
        audience=kw.get("aud", "seatflow-api"),
    )


def test_access_token_roundtrip(jwt_keys):
    t = _issue(jwt_keys, datetime.now(UTC))
    claims = _decode(jwt_keys, t.token)
    assert set(claims) >= {"iss", "aud", "sub", "iat", "nbf", "exp", "jti"}


def test_expired_token_rejected(jwt_keys):
    t = _issue(jwt_keys, datetime.now(UTC) - timedelta(hours=1))
    with pytest.raises(jwt.ExpiredSignatureError):
        _decode(jwt_keys, t.token)


def test_wrong_audience_rejected(jwt_keys):
    t = _issue(jwt_keys, datetime.now(UTC))
    with pytest.raises(jwt.InvalidTokenError):
        _decode(jwt_keys, t.token, aud="other")


def test_alg_none_and_hs256_rejected(jwt_keys):
    claims = {"sub": "x", "iss": "seatflow", "aud": "seatflow-api"}
    forged_none = jwt.encode(claims, None, algorithm="none")
    with pytest.raises(jwt.InvalidTokenError):
        _decode(jwt_keys, forged_none)
    forged_hs = jwt.encode(claims, "some-secret", algorithm="HS256")
    with pytest.raises(jwt.InvalidTokenError):
        _decode(jwt_keys, forged_hs)


def test_digest_and_tokens():
    a, b = sec.new_token(), sec.new_token()
    assert a != b and len(a) >= 43
    assert sec.digest_matches(a, sec.digest(a))
    assert not sec.digest_matches(b, sec.digest(a))


def test_ip_prefix():
    assert sec.ip_prefix("203.0.113.77") == "203.0.113.0/24"
    assert sec.ip_prefix("2001:db8:1:2::1") == "2001:db8:1::/48"
    assert sec.ip_prefix("not-an-ip") is None
