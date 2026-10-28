"""Logging setup with secret redaction.

Every log record passes through :class:`RedactingFilter`, which masks anything
that looks like a Seats.aero API key or a bearer token so a key can never leak
into a log file even if a developer logs a request object by mistake.
"""
from __future__ import annotations

import logging
import os
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

_SECRET_ENV_VARS = ("SEATS_AERO_API_KEY", "SMTP_PASSWORD", "ALERT_WEBHOOK_URL")
_BEARER_RE = re.compile(r"(Bearer\s+)([A-Za-z0-9._\-]{8,})", re.IGNORECASE)
_PRO_KEY_RE = re.compile(r"\bpro_[A-Za-z0-9]{12,}\b")


def mask_secret(value: str | None) -> str:
    """Return a safe display form of a secret: first 4 chars + ... + last 2."""
    if not value:
        return "<not set>"
    if len(value) <= 8:
        return "****"
    return f"{value[:4]}...{value[-2:]}"


class RedactingFilter(logging.Filter):
    """Masks configured secret values and bearer tokens inside log messages."""

    def __init__(self, secrets: list[str] | None = None) -> None:
        super().__init__()
        self._secrets = [s for s in (secrets or []) if s and len(s) >= 8]

    def refresh_from_env(self) -> None:
        self._secrets = [
            v for v in (os.environ.get(name) for name in _SECRET_ENV_VARS) if v and len(v) >= 8
        ]

    def _scrub(self, text: str) -> str:
        for secret in self._secrets:
            if secret in text:
                text = text.replace(secret, mask_secret(secret))
        text = _BEARER_RE.sub(lambda m: m.group(1) + "[REDACTED]", text)
        text = _PRO_KEY_RE.sub("[REDACTED_KEY]", text)
        return text

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        try:
            msg = record.getMessage()
        except Exception:  # pragma: no cover - defensive
            return True
        record.msg = self._scrub(msg)
        record.args = ()
        return True


_configured = False


def configure_logging(level: str = "INFO", log_dir: str | Path = "logs") -> None:
    """Configure console + rotating-file logging once per process."""
    global _configured
    if _configured:
        return
    redactor = RedactingFilter()
    redactor.refresh_from_env()

    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    console.addFilter(redactor)
    root.addHandler(console)

    try:
        Path(log_dir).mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            Path(log_dir) / "award_hunter.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(fmt)
        file_handler.addFilter(redactor)
        root.addHandler(file_handler)
    except OSError:  # pragma: no cover - read-only filesystems
        pass

    # Quiet noisy libraries; httpx logs full URLs at INFO.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    _configured = True
