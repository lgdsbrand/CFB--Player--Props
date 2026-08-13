"""Logging configuration for worker jobs."""

from __future__ import annotations

import logging
import sys

from worker.config import redact_secrets

LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
DATE_FORMAT = "%Y-%m-%dT%H:%M:%S"


class RedactingFormatter(logging.Formatter):
    """Formatter that removes database passwords from every line it emits.

    A Formatter rather than a Filter, and that choice is the point: a Filter sees
    the record before the traceback is rendered, so `log.exception(...)` and any
    handler-formatted `exc_info` would slip a leaked DSN straight past it.
    `Formatter.format` returns the finished string — message, traceback and all —
    which is the only place where redacting once covers every path.

    Why this is centralised instead of fixed at the call site: libpq puts the
    connection string inside its own error text (see `config.redact_secrets`), so
    the leak rides on ordinary, correct code — `log.error("...: %s", exc)`. There
    is no list of call sites to audit, present or future, so the guard belongs
    where all of them converge.

    This is defence in depth, not the primary fix. `db.connect` redacts the
    exception itself, so the password does not reach a log line, a `pipeline_runs`
    row, or an unhandled traceback (which Python prints without consulting
    logging at all). This catches whatever that misses.
    """

    def format(self, record: logging.LogRecord) -> str:
        return redact_secrets(super().format(record))


def configure_logging(level: str = "INFO") -> None:
    """Configure root logging for a job process.

    Render captures stdout/stderr, so a plain stream handler is all that is
    needed. Timestamps are included because the pipeline is cron-driven and log
    lines are read out of order.
    """
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format=LOG_FORMAT,
        datefmt=DATE_FORMAT,
        stream=sys.stdout,
        force=True,
    )
    # basicConfig has no hook for a custom formatter class, and `force=True`
    # means the handler it just built is the only one — so swap the formatter on
    # whatever it created rather than assembling the handler by hand.
    for handler in logging.getLogger().handlers:
        handler.setFormatter(RedactingFormatter(LOG_FORMAT, DATE_FORMAT))

    # The CFBD client logs every request at INFO; too noisy for a backfill.
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
