"""Security primitives: Argon2id password hashing, EdDSA access-token signing
and verification, random token generation and SHA-256 token digests.

Pure functions over their inputs; loading accounts is done by services.

Design: TECHNICAL_DESIGN.md Sections 9.1 and 9.2.
"""

import hashlib
import hmac
import ipaddress
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

ALGORITHM = "EdDSA"
_hasher = PasswordHasher()

_DUMMY_HASH = _hasher.hash("dummy-password-for-timing")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


@dataclass(frozen=True, slots=True)
class AccessToken:
    token: str
    expires_at: datetime
    expires_in: int


def issue_access_token(
    *,
    account_id: uuid.UUID,
    now: datetime,
    private_key: str,
    issuer: str,
    audience: str,
    ttl_seconds: int,
) -> AccessToken:
    exp = now + timedelta(seconds=ttl_seconds)
    claims = {
        "iss": issuer,
        "aud": audience,
        "sub": str(account_id),
        "iat": int(now.timestamp()),
        "nbf": int(now.timestamp()),
        "exp": int(exp.timestamp()),
        "jti": str(uuid.uuid4()),
    }
    token = jwt.encode(claims, private_key, algorithm=ALGORITHM)
    return AccessToken(token=token, expires_at=exp, expires_in=ttl_seconds)


def decode_access_token(token: str, *, public_key: str, issuer: str, audience: str) -> dict:
    return jwt.decode(
        token,
        public_key,
        algorithms=[ALGORITHM],
        issuer=issuer,
        audience=audience,
        options={"require": ["iss", "aud", "sub", "iat", "nbf", "exp", "jti"]},
        leeway=5,
    )


def new_token() -> str:
    return secrets.token_urlsafe(32)


def digest(value: str) -> bytes:
    return hashlib.sha256(value.encode()).digest()


def digest_matches(value: str, expected: bytes) -> bool:
    return hmac.compare_digest(digest(value), expected)


def ip_prefix(ip: str | None) -> str | None:
    if not ip:
        return None
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return None
    bits = 24 if addr.version == 4 else 48
    return str(ipaddress.ip_network(f"{addr}/{bits}", strict=False))
