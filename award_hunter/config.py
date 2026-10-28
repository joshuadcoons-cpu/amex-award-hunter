"""Application configuration loaded from environment / .env.

The API key is read only from the environment (optionally populated from a local
``.env`` file). It is never written to the database, never logged in full and
never placed in a URL.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SEATS_AERO_BASE_URL = "https://seats.aero/partnerapi"


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc


@dataclass
class Settings:
    """Runtime settings. Build with :func:`load_settings`."""

    api_key: str | None
    auth_scheme: str = "bearer"
    base_url: str = SEATS_AERO_BASE_URL
    database_path: Path = PROJECT_ROOT / "data" / "awards.sqlite"
    timezone: str = "America/Los_Angeles"
    api_daily_budget: int = 800
    request_timeout_seconds: float = 30.0
    max_retries: int = 4
    alert_webhook_url: str | None = None
    alert_email_to: str | None = None
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None
    ntfy_topic: str | None = None
    ntfy_server: str = "https://ntfy.sh"
    macos_notifications: bool = True
    amadeus_client_id: str | None = None
    amadeus_client_secret: str | None = None
    amadeus_env: str = "test"
    log_level: str = "INFO"
    extra: dict = field(default_factory=dict)

    @property
    def has_api_key(self) -> bool:
        return bool(self.api_key) and self.api_key != "replace_with_your_key"

    def require_api_key(self) -> str:
        if not self.has_api_key:
            raise ConfigError(
                "SEATS_AERO_API_KEY is not configured. Create a .env file next to app.py "
                "(copy .env.example) and paste your Seats.aero Pro API key into it."
            )
        assert self.api_key is not None
        return self.api_key

    @property
    def alerts_enabled(self) -> bool:
        return (bool(self.alert_webhook_url) or bool(self.alert_email_to and self.smtp_host) or bool(self.ntfy_topic)
                or self.macos_notifications)


def load_settings(env_file: str | Path | None = None, *, override_env: bool = False) -> Settings:
    """Load settings from ``.env`` (if present) and the process environment."""
    dotenv_path = Path(env_file) if env_file else PROJECT_ROOT / ".env"
    if dotenv_path.exists():
        load_dotenv(dotenv_path, override=override_env)

    api_key = os.environ.get("SEATS_AERO_API_KEY", "").strip() or None
    scheme = os.environ.get("SEATS_AERO_AUTH_SCHEME", "bearer").strip().lower() or "bearer"
    if scheme not in {"bearer", "raw"}:
        raise ConfigError("SEATS_AERO_AUTH_SCHEME must be 'bearer' or 'raw'")

    db_raw = os.environ.get("DATABASE_PATH", "data/awards.sqlite").strip() or "data/awards.sqlite"
    db_path = Path(db_raw)
    if not db_path.is_absolute():
        db_path = PROJECT_ROOT / db_path

    def opt(name: str) -> str | None:
        value = os.environ.get(name, "").strip()
        return value or None

    return Settings(
        api_key=api_key,
        auth_scheme=scheme,
        base_url=os.environ.get("SEATS_AERO_BASE_URL", SEATS_AERO_BASE_URL).rstrip("/"),
        database_path=db_path,
        timezone=os.environ.get("TIMEZONE", "America/Los_Angeles").strip() or "America/Los_Angeles",
        api_daily_budget=_env_int("API_DAILY_BUDGET", 800),
        request_timeout_seconds=float(os.environ.get("API_TIMEOUT_SECONDS", "30") or 30),
        max_retries=_env_int("API_MAX_RETRIES", 4),
        alert_webhook_url=opt("ALERT_WEBHOOK_URL"),
        alert_email_to=opt("ALERT_EMAIL_TO"),
        smtp_host=opt("SMTP_HOST"),
        smtp_port=_env_int("SMTP_PORT", 587),
        smtp_username=opt("SMTP_USERNAME"),
        smtp_password=opt("SMTP_PASSWORD"),
        smtp_from=opt("SMTP_FROM"),
        ntfy_topic=opt("NTFY_TOPIC"),
        ntfy_server=opt("NTFY_SERVER") or "https://ntfy.sh",
        macos_notifications=(os.environ.get("ALERT_MACOS_NOTIFICATIONS", "1").strip().lower() not in {"0", "false", "no", "off"}),
        amadeus_client_id=opt("AMADEUS_CLIENT_ID"),
        amadeus_client_secret=opt("AMADEUS_CLIENT_SECRET"),
        amadeus_env=(opt("AMADEUS_ENV") or "test").lower(),
        log_level=os.environ.get("LOG_LEVEL", "INFO").strip() or "INFO",
    )


def validate_security(settings: Settings) -> list[str]:
    """Return a list of human-readable security warnings (empty when clean)."""
    warnings: list[str] = []
    env_path = PROJECT_ROOT / ".env"
    gitignore = PROJECT_ROOT / ".gitignore"
    if env_path.exists() and gitignore.exists():
        if ".env" not in gitignore.read_text(encoding="utf-8").split():
            warnings.append(".env is not listed in .gitignore - your API key could be committed.")
    if settings.api_key and len(settings.api_key) < 16:
        warnings.append("SEATS_AERO_API_KEY looks too short to be a real key.")
    if settings.api_key and settings.api_key.lower().startswith("bearer "):
        warnings.append("SEATS_AERO_API_KEY should be the bare key, without the word 'Bearer'.")
    return warnings
