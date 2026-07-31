"""PostgreSQL access for the worker.

The worker owns all writes. The Next.js app only ever reads, and there are no
write policies in the schema, so this module is the sole path by which data
enters Supabase (CLAUDE.md §2).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg.rows import dict_row

from worker.config import get_settings
from worker.logging_setup import get_logger

log = get_logger(__name__)


@contextmanager
def connect(autocommit: bool = False) -> Iterator[psycopg.Connection]:
    """Open a connection to the project database."""
    settings = get_settings()
    with psycopg.connect(
        settings.database_url,
        autocommit=autocommit,
        row_factory=dict_row,
    ) as conn:
        yield conn


def fetch_one(sql: str, params: tuple[Any, ...] | None = None) -> dict[str, Any] | None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def fetch_all(sql: str, params: tuple[Any, ...] | None = None) -> list[dict[str, Any]]:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def get_config_value(key: str) -> Any:
    """Read a single value from app_config.

    Runtime configuration lives in the database so the two decisions still open
    with the client (odds source, hit-rate basis — CLAUDE.md §9) can be changed
    without a deploy.
    """
    row = fetch_one("select value from app_config where key = %s", (key,))
    return row["value"] if row else None


@contextmanager
def pipeline_run(job_name: str, metadata: dict[str, Any] | None = None) -> Iterator[uuid.UUID]:
    """Record a job execution in pipeline_runs.

    Opens a `running` row, marks it `succeeded` on clean exit, or `failed` with
    the exception text on error — then re-raises. Backs the pipeline
    monitoring/alerting deliverable in CLAUDE.md §8 Phase 5, and in Phase 1 it
    doubles as proof that the worker's credentials and write path work.

    Uses its own autocommit connection so the bookkeeping row survives even when
    the job's own transaction rolls back.
    """
    run_id = uuid.uuid4()
    with connect(autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into pipeline_runs (id, job_name, status, metadata)
                values (%s, %s, 'running', %s::jsonb)
                """,
                (run_id, job_name, psycopg.types.json.Json(metadata or {})),
            )
        log.info("pipeline_run %s started (%s)", run_id, job_name)

        try:
            yield run_id
        except BaseException as exc:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    update pipeline_runs
                       set status = 'failed',
                           finished_at = now(),
                           error = %s
                     where id = %s
                    """,
                    (f"{type(exc).__name__}: {exc}", run_id),
                )
            log.error("pipeline_run %s failed: %s", run_id, exc)
            raise
        else:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    update pipeline_runs
                       set status = 'succeeded',
                           finished_at = now()
                     where id = %s
                    """,
                    (run_id,),
                )
            log.info("pipeline_run %s succeeded", run_id)


def set_rows_written(run_id: uuid.UUID, rows: int) -> None:
    """Record how many rows a job wrote, for the ingest row-count deliverable."""
    with connect(autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "update pipeline_runs set rows_written = %s where id = %s",
            (rows, run_id),
        )
