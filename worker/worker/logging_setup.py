"""Logging configuration for worker jobs."""

from __future__ import annotations

import logging
import sys


def configure_logging(level: str = "INFO") -> None:
    """Configure root logging for a job process.

    Render captures stdout/stderr, so a plain stream handler is all that is
    needed. Timestamps are included because the pipeline is cron-driven and log
    lines are read out of order.
    """
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        stream=sys.stdout,
        force=True,
    )
    # The CFBD client logs every request at INFO; too noisy for a backfill.
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
