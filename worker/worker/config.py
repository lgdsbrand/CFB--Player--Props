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
import re
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


# =============================================================================
# Redacting a connection string out of text that is about to be logged
# =============================================================================
# `_SECRET_FIELDS` above guards exactly one thing: `repr(Settings)`. It does
# nothing for the leak this section exists for, because that leak never touches
# Settings.
#
# libpq echoes the connection string back INSIDE its own error message, so a
# connection failure hands every caller an exception whose text contains the
# password. Observed in production on 2026-08-12: a mistyped scheme
# (`postgreSQL://`, which libpq does not recognise as a URI, so it falls through
# to keyword=value parsing) produced
#
#     missing "=" after "postgreSQL://postgres.<ref>:<password>@..." in ...
#
# and `healthcheck` logged it verbatim into Render's log, which is retained and
# readable by anyone with dashboard access. The bug is not in healthcheck; any
# of the 18 jobs would have done the same, because logging an exception is the
# correct thing to do with one.
_URI_PASSWORD_RE = re.compile(
    r"(?i)(?P<prefix>postgres(?:ql)?://[^:@/\s]*:)(?P<password>[^@\s]*)(?P<at>@)"
)
_KEYWORD_PASSWORD_RE = re.compile(
    r"(?i)\bpassword\s*=\s*(?:'[^']*'|\"[^\"]*\"|\S+)"
)

# Env vars whose whole value is a connection string. Their values are also
# removed literally, as a second pass, because the patterns above can only match
# a DSN that is still WELL FORMED — and a malformed DSN is precisely the case
# that produces the error message in the first place. The two passes cover
# different failures and neither subsumes the other: the pattern catches a
# correct DSN in an unexpected place, the literal catches a mangled one.
_DSN_ENV_VARS = ("SUPABASE_DB_URL", "MIGRATION_TARGET_DB_URL")

# Floor for literal substring removal. A short "password" is too plausibly an
# ordinary word that also occurs in the surrounding message, and blanket removal
# would corrupt the very error someone is trying to read. Supabase-generated
# passwords are far longer than this, so nothing real is skipped.
_MIN_LITERAL_SECRET_LEN = 12


def _literal_dsn_secrets() -> list[str]:
    """Connection strings and their passwords, read straight from the environment.

    Deliberately not via `get_settings()`: this has to work when config loading
    itself failed, which is the same neighbourhood of failures that leaks a DSN.
    Longest first, so a full DSN is consumed before its own password fragment is
    matched inside what remains.
    """
    found: set[str] = set()
    for name in _DSN_ENV_VARS:
        value = (os.environ.get(name) or "").strip()
        if len(value) >= _MIN_LITERAL_SECRET_LEN:
            found.add(value)
        match = _URI_PASSWORD_RE.search(value)
        if match and len(match.group("password")) >= _MIN_LITERAL_SECRET_LEN:
            found.add(match.group("password"))
    return sorted(found, key=len, reverse=True)


def redact_secrets(text: str) -> str:
    """Strip database passwords out of a string before it is logged or stored.

    Structural counterpart to `alerts/webhook._safe`, which removes a webhook URL
    the same way and for the same reason. Kept in this module because this is
    where the knowledge of what counts as a secret already lives.
    """
    redacted = _URI_PASSWORD_RE.sub(r"\g<prefix><redacted>\g<at>", text)
    redacted = _KEYWORD_PASSWORD_RE.sub("password=<redacted>", redacted)
    for literal in _literal_dsn_secrets():
        redacted = redacted.replace(literal, "<redacted>")
    return redacted


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


def env_names_containing(fragment: str) -> list[str]:
    """NAMES — never values — of environment variables containing `fragment`.

    A near-miss detector for credential handover. "ODDS_API_KEY is not set"
    cannot distinguish *absent* from *present under the wrong name*, and on
    2026-09-03 that ambiguity cost six days: the client had added the key to
    Render as `ODDS_API_VARIABLE`, and nine consecutive failed runs reported
    only that `ODDS_API_KEY` was missing. Listing what the process CAN see
    turns a guess into a reading.

    NAMES ONLY, AND THAT IS LOAD-BEARING. `monitor_pipeline` copies the error
    text of a failed run into the alert body, and the webhook alert adapter
    sends that body to a third party — so a value here would leave the system.
    `redact_secrets` would not save us: it strips DSNs and their passwords, not
    API keys.
    """
    needle = fragment.upper()
    return sorted(name for name in os.environ if needle in name.upper())


_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"0", "false", "no", "off", ""})


def _flag(name: str) -> bool:
    """Read a boolean environment variable, refusing anything ambiguous.

    Deliberately strict. The one flag this reads decides which of the client's
    two odds allowances gets billed, and the paid one is shared with three
    other models. A typo like ODDS_PREFER_FREE="ture" silently spending the
    shared pool is precisely the failure this raises to prevent, so an
    unrecognised value is a configuration error rather than a falsy default.
    """
    raw = os.environ.get(name)
    if raw is None:
        return False
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise ConfigError(
        f"{name}={raw!r} is not a boolean. Use one of "
        f"{sorted(_TRUE)} or {sorted(_FALSE)}."
    )


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

    # Bill the free key without editing render.yaml. The odds cron's command is
    # fixed in the blueprint and its schedule cannot be made one-shot -- any
    # dow-restricted cron reads as 168h to the monitor's guards -- so the
    # opening-weekend fallback has to be switchable at runtime, from the Render
    # dashboard, and revertible the same way. `--free` is the CLI equivalent.
    odds_prefer_free: bool = False

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
        odds_prefer_free=_flag("ODDS_PREFER_FREE"),
        gemini_api_key=_optional("GEMINI_API_KEY"),
        grok_api_key=_optional("GROK_API_KEY"),
        alert_webhook_url=_optional("ALERT_WEBHOOK_URL"),
        environment=_optional("ENVIRONMENT", "development") or "development",
        log_level=_optional("LOG_LEVEL", "INFO") or "INFO",
    )
