"""The database password must not reach a log line, a stored error, or a traceback.

Regression tests for a leak observed in production on 2026-08-12: libpq echoes
the connection string inside its own error message, so a connection failure hands
the caller an exception whose text contains the password — and every job logs
exceptions, because logging an exception is the correct thing to do with one.

The fixture DSNs below are invented. `_MIN_LITERAL_SECRET_LEN` in config means a
password shorter than 12 characters is not removed literally, so the fakes here
are realistically long rather than "hunter2".
"""

from __future__ import annotations

import logging
import traceback

import psycopg
import pytest

from worker.config import redact_secrets
from worker.logging_setup import RedactingFormatter

FAKE_PASSWORD = "Xk93qLmZ7vRt2wBn"  # noqa: S105 - fixture, not a credential
FAKE_DSN = (
    f"postgresql://postgres.abcdefghijklmnop:{FAKE_PASSWORD}"
    "@aws-0-ca-central-1.pooler.supabase.com:5432/postgres"
)


class TestRedactSecrets:
    def test_a_well_formed_dsn_loses_its_password(self):
        assert FAKE_PASSWORD not in redact_secrets(FAKE_DSN)

    def test_the_rest_of_the_dsn_survives(self):
        """Redaction must not destroy the diagnostic value of the message.

        Host, port, database and user are what someone reads to work out which
        end failed and why. Removing the whole DSN would be safe and useless.
        """
        cleaned = redact_secrets(FAKE_DSN)
        assert "aws-0-ca-central-1.pooler.supabase.com:5432" in cleaned
        assert "postgres.abcdefghijklmnop" in cleaned
        assert "<redacted>" in cleaned

    def test_the_exact_production_error_is_redacted(self):
        """The observed leak, verbatim in shape.

        `postgreSQL://` with a capital scheme is not recognised by libpq as a
        URI, so it falls through to keyword=value parsing and reports a missing
        "=" — quoting the whole string, password included. A case-sensitive
        pattern would let precisely this line through.
        """
        message = (
            f'missing "=" after "postgreSQL://postgres.abcdefghijklmnop:'
            f'{FAKE_PASSWORD}@aws-0-ca-central-1.pooler.supabase.com:5432/'
            'postgres" in connection info string'
        )
        assert FAKE_PASSWORD not in redact_secrets(message)

    def test_a_keyword_value_password_is_redacted(self):
        """libpq's other syntax, which is what a mistyped URI degrades into."""
        assert FAKE_PASSWORD not in redact_secrets(
            f"host=db.example.com password={FAKE_PASSWORD} dbname=postgres"
        )

    def test_a_quoted_keyword_value_password_is_redacted(self):
        assert FAKE_PASSWORD not in redact_secrets(f"password='{FAKE_PASSWORD}' host=x")

    def test_a_mangled_dsn_is_still_redacted_via_the_environment(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """The case the pattern pass cannot catch, and why the literal pass exists.

        The client's second paste dropped the `@`. With no delimiter there is no
        password-shaped fragment to match, so the only thing that can save the
        line is knowing the configured value and removing it literally.
        """
        mangled = FAKE_DSN.replace("@", "")
        monkeypatch.setenv("SUPABASE_DB_URL", mangled)
        assert FAKE_PASSWORD not in redact_secrets(
            f'missing "=" after "{mangled}" in connection info string'
        )

    def test_the_production_url_is_redacted_too(self, monkeypatch: pytest.MonkeyPatch):
        """migrate_database holds both ends; the target is the more costly one."""
        monkeypatch.setenv("MIGRATION_TARGET_DB_URL", FAKE_DSN)
        assert FAKE_PASSWORD not in redact_secrets(f"connection to {FAKE_DSN} failed")

    def test_ordinary_text_is_untouched(self):
        """A redactor that mangles innocent messages gets switched off."""
        message = "Allar projects under 238.5 on a line of 240.5"
        assert redact_secrets(message) == message

    def test_a_short_password_is_not_removed_literally(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """The floor on literal removal, stated as a test so it is a decision.

        A DSN password of "postgres" would otherwise be excised from every
        message that mentions Postgres at all. The pattern pass still redacts it
        when it appears in DSN position; only blanket substring removal is
        skipped.
        """
        monkeypatch.setenv("SUPABASE_DB_URL", "postgresql://u:short@h:5432/postgres")
        assert redact_secrets("the short form of the query") == (
            "the short form of the query"
        )


class TestRedactingFormatter:
    """The logging-layer net, for the paths `db.connect` does not own."""

    def _render(self, record: logging.LogRecord) -> str:
        return RedactingFormatter("%(message)s").format(record)

    def test_a_logged_message_is_redacted(self):
        record = logging.LogRecord(
            "x", logging.ERROR, __file__, 1, "connecting to %s", (FAKE_DSN,), None
        )
        assert FAKE_PASSWORD not in self._render(record)

    def test_a_traceback_is_redacted(self):
        """Why this is a Formatter and not a Filter.

        A Filter runs before `exc_info` is rendered, so a password inside the
        exception text would be invisible to it and print anyway.
        """
        try:
            raise ValueError(f'missing "=" after "{FAKE_DSN}"')
        except ValueError:
            import sys

            record = logging.LogRecord(
                "x", logging.ERROR, __file__, 1, "failed", (), sys.exc_info()
            )
        rendered = self._render(record)
        assert "Traceback" in rendered
        assert FAKE_PASSWORD not in rendered


class TestConnectDoesNotLeak:
    def test_a_bad_dsn_raises_without_the_password(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """End to end through the real chokepoint, against real libpq.

        No network: the DSN is rejected during parsing, which is the failure mode
        that leaked. Asserting on psycopg's actual behaviour rather than a mock is
        the point — the leak was libpq's message, so a fake message proves nothing.
        """
        from worker import config, db

        mangled = f"postgreSQL://postgres.abc:{FAKE_PASSWORD}@host:5432/postgres"
        monkeypatch.setenv("SUPABASE_DB_URL", mangled)
        config.get_settings.cache_clear()

        with pytest.raises(db.DatabaseConnectionError) as raised:
            with db.connect():
                pass  # pragma: no cover - connect raises first

        config.get_settings.cache_clear()
        assert FAKE_PASSWORD not in str(raised.value)

        # The message alone is not the guarantee. `raise ... from None` would
        # pass the assertion above while leaving the original psycopg exception
        # on `__context__` with the password intact, reachable by anything that
        # walks the chain. Assert on the fully rendered traceback, chain
        # included, which is what a log or a crash actually prints.
        rendered = "".join(
            traceback.format_exception(
                type(raised.value), raised.value, raised.value.__traceback__
            )
        )
        assert FAKE_PASSWORD not in rendered
        assert raised.value.__cause__ is None
        assert raised.value.__context__ is None

    def test_the_message_still_explains_the_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        from worker import config, db

        monkeypatch.setenv("SUPABASE_DB_URL", f"postgreSQL://u:{FAKE_PASSWORD}@h:5432/d")
        config.get_settings.cache_clear()
        with pytest.raises(db.DatabaseConnectionError) as raised:
            with db.connect():
                pass  # pragma: no cover
        config.get_settings.cache_clear()
        assert "Could not connect to the database" in str(raised.value)


def test_psycopg_really_does_echo_the_dsn(monkeypatch: pytest.MonkeyPatch):
    """Proof the hazard is real, not a defence against an imagined threat.

    If a future psycopg or libpq stops quoting the connection string, this test
    fails and the redaction above can be reconsidered on evidence. Until then it
    documents why all of this exists.
    """
    with pytest.raises(psycopg.Error) as raised:
        psycopg.connect(f"postgreSQL://u:{FAKE_PASSWORD}@h:5432/d")
    assert FAKE_PASSWORD in str(raised.value)
