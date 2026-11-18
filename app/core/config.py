"""Settings for every process, read from environment variables and validated
at startup. A missing or malformed value stops the process.

This is the only module that reads the environment. Field names map
case-insensitively to the variables in the design table. Secrets without
defaults (JWT keys, bootstrap administrator) are added with the
authentication work; values the design fixes live in ``app.core.constants``.

Design: TECHNICAL_DESIGN.md Section 13.5.
"""

from functools import cached_property, lru_cache
from ipaddress import IPv4Network, IPv6Network, ip_network
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: Literal["local", "minikube", "doks"] = "local"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    database_url: str  # required: startup fails if missing
    db_pool_size: int = Field(default=5, ge=1)
    db_max_overflow: int = Field(default=5, ge=0)
    redis_url: str = "redis://redis:6379/0"

    hold_validity_seconds: int = Field(default=300, gt=0)
    offer_validity_seconds: int = Field(default=600, gt=0)
    registration_write_limit_per_minute: int = Field(default=30, gt=0)
    login_limit_per_minute: int = Field(default=10, gt=0)

    trusted_proxy_cidrs: str = ""
    cors_allowed_origins: str = "http://localhost:5173"

    jwt_issuer: str = "seatflow"
    jwt_audience: str = "seatflow-api"
    access_token_ttl_seconds: int = Field(default=900, gt=0)
    refresh_token_ttl_seconds: int = Field(default=604800, gt=0)

    worker_scan_interval_seconds: float = Field(default=1, gt=0)
    worker_scan_batch_size: int = Field(default=100, gt=0)
    worker_metrics_port: int = Field(default=9100, ge=1, le=65535)

    # Optional here so Alembic and tests can build Settings; the API checks them at startup.
    jwt_private_key: SecretStr | None = None   # Ed25519 PEM (PKCS8)
    jwt_public_key: str | None = None          # Ed25519 PEM (SubjectPublicKeyInfo)

    bootstrap_admin_email: str | None = None
    bootstrap_admin_password: SecretStr | None = None

    @field_validator("trusted_proxy_cidrs")
    @classmethod
    def _valid_cidrs(cls, v: str) -> str:
        _parse_cidrs(v)
        return v

    @cached_property
    def trusted_proxies(self) -> tuple[IPv4Network | IPv6Network, ...]:
        """``TRUSTED_PROXY_CIDRS`` parsed: comma-separated CIDRs of the gateway."""
        return _parse_cidrs(self.trusted_proxy_cidrs)

    def require_jwt_keys(self) -> tuple[str, str]:
        if self.jwt_private_key is None or self.jwt_public_key is None:
            raise RuntimeError("JWT_PRIVATE_KEY and JWT_PUBLIC_KEY must be set for the API")
        return self.jwt_private_key.get_secret_value(), self.jwt_public_key


def _parse_cidrs(value: str) -> tuple[IPv4Network | IPv6Network, ...]:
    return tuple(ip_network(part.strip(), strict=False) for part in value.split(",") if part.strip())


@lru_cache
def get_settings() -> Settings:
    return Settings()
