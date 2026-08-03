"""Environment-driven configuration.

Every credential comes from the environment. No default in this module may ever
be a real key, URL with embedded credentials, or anything else secret — this
repo is treated as public-adjacent and key hygiene is a hard rule (CLAUDE.md
§0).

`Settings.__repr__` is overridden to redact secrets, because the default
dataclass repr would happily print an API key into a log line.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, fields
from functools import lru_cache
from pathlib import Path

try:  # optional: absent in production, where Render injects env vars directly
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None  # type: ignore[assignment]


REPO_ROOT = Path(__file__).resolve().parents[2]

# Field names whose values must never be logged or printed.
_SECRET_FIELDS = frozenset(
    {
        "database_url",
        "cfbd_api_key",
        "supabase_service_role_key",
        "odds_api_key",
        "odds_api_key_free",
        "gemini_api_key",
        "grok_api_key",
        # The whole URL is the secret. A Slack incoming-webhook URL carries its
        # credential in the path, so this is a key that happens to look like an
        # address — treat it as one.
        "alert_webhook_url",
    }
)


class ConfigError(RuntimeError):
    """Raised when a required environment variable is missing or unusable."""


def _load_dotenv_if_present() -> None:
    if load_dotenv is None:
        return
    for candidate in (REPO_ROOT / ".env", REPO_ROOT / "worker" / ".env"):
        if candidate.exists():
            load_dotenv(candidate, override=False)


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ConfigError(
            f"Missing required environment variable {name!r}. "
            f"Copy .env.example to .env and fill it in."
        )
    return value


def _optional(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    return value if value else default


@dataclass(frozen=True)
class Settings:
    """Resolved worker configuration."""

    # Required for anything at all
    database_url: str

    # Required for CFBD ingest, but loading config must not fail without it:
    # during first-time setup the database exists before the API key does, and
    # the healthcheck needs to be able to report "database OK, CFBD key missing"
    # rather than refusing to start. Use require_cfbd_api_key() at the point of
    # use instead.
    cfbd_api_key: str | None

    # Optional — needed by later phases
    supabase_url: str | None
    supabase_service_role_key: str | None
    odds_api_key: str | None

    # Weekly AI reads (CLAUDE.md §7). Which one is actually used is decided by
    # `app_config.ai_adapter`, NOT by which of these happens to be set — the
    # provider is configuration, like the odds source. Both are optional so the
    # worker starts with neither; the null adapter is a real selectable option
    # and the app degrades to its existing empty read slot.
    gemini_api_key: str | None = None
    grok_api_key: str | None = None

    # Pipeline alerting (CLAUDE.md §8 Phase 5). Optional: the default alert
    # adapter writes to the run log and needs no configuration, so the monitor
    # works out of the box and this only matters once someone wants a push.
    # NEVER put this in app_config — that table is world-readable under RLS.
    alert_webhook_url: str | None = None

    # Separate free-tier key, for dry runs. The paid allowance is a SHARED pool
    # across the client's other models, and it has already been exhausted once
    # mid-month — so proving the adapter works must not be able to spend from it
    # by accident. Optional: falls back to the main key when unset.
    odds_api_key_free: str | None = None

    environment: str = "development"
    log_level: str = "INFO"

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    def odds_key(self, *, prefer_free: bool = False) -> str | None:
        """Pick which odds key to spend against.

        Falls back to the paid key when no free key is configured, so `--free`
        degrades to "run it anyway" rather than failing. The probe logs which
        one it used, because a coverage finding means nothing without knowing
        which tier produced it.
        """
        if prefer_free and self.odds_api_key_free:
            return self.odds_api_key_free
        return self.odds_api_key

    def require_cfbd_api_key(self) -> str:
        """Return the CFBD key, raising ConfigError with guidance if unset."""
        if not self.cfbd_api_key:
            raise ConfigError(
                "CFBD_API_KEY is not set. Get one at https://collegefootballdata.com/key "
                "— a paid tier is assumed, since the free tier rate-limits too hard "
                "for a multi-season all-FBS backfill."
            )
        return self.cfbd_api_key

    def __repr__(self) -> str:
        parts = []
        for field in fields(self):
            value = getattr(self, field.name)
            if field.name in _SECRET_FIELDS:
                shown = "<set>" if value else "<unset>"
            else:
                shown = repr(value)
            parts.append(f"{field.name}={shown}")
        return f"Settings({', '.join(parts)})"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load and cache settings. Raises ConfigError if required values missing."""
    _load_dotenv_if_present()
    return Settings(
        database_url=_require("SUPABASE_DB_URL"),
        cfbd_api_key=_optional("CFBD_API_KEY"),
        supabase_url=_optional("SUPABASE_URL"),
        supabase_service_role_key=_optional("SUPABASE_SERVICE_ROLE_KEY"),
        odds_api_key=_optional("ODDS_API_KEY"),
        odds_api_key_free=_optional("ODDS_API_KEY_FREE"),
        gemini_api_key=_optional("GEMINI_API_KEY"),
        grok_api_key=_optional("GROK_API_KEY"),
        alert_webhook_url=_optional("ALERT_WEBHOOK_URL"),
        environment=_optional("ENVIRONMENT", "development") or "development",
        log_level=_optional("LOG_LEVEL", "INFO") or "INFO",
    )
