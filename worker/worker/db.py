"""PostgreSQL access for the worker.

The worker owns all writes. The Next.js app only ever reads, and there are no
write policies in the schema, so this module is the sole path by which data
enters Supabase (CLAUDE.md §2).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg import sql
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


def upsert(
    table: str,
    rows: Sequence[Mapping[str, Any]],
    *,
    conflict_columns: Sequence[str],
    update_columns: Sequence[str] | None = None,
    batch_size: int = 1000,
) -> int:
    """Bulk INSERT ... ON CONFLICT DO UPDATE. Returns rows affected.

    Ingest is re-run constantly during development and weekly in production, so
    every write has to be idempotent — a second run must correct rows rather
    than duplicate them or abort.

    Rows may have differing key sets; the union is used and missing keys are
    written as NULL. That matters because CFBD omits fields rather than sending
    nulls, so an early-season game and a completed one do not have identical
    shapes, and requiring uniformity would push per-row conditionals into every
    caller.

    Passing an empty `update_columns` makes this DO NOTHING, for append-only
    tables where an existing row must never be rewritten.
    """
    rows = list(rows)
    if not rows:
        return 0

    # Postgres rejects an entire INSERT ... ON CONFLICT statement if two rows in
    # it share a conflict key ("cannot affect row a second time"), so a single
    # duplicate anywhere in a batch kills the whole job. CFBD does emit
    # duplicates — /conferences returns historical and current records under the
    # same name. Collapse them here, last occurrence winning, and say so, since
    # silently discarding a row is exactly the kind of thing that should appear
    # in a log rather than be inferred later from a row count that looks short.
    deduped: dict[tuple[Any, ...], Mapping[str, Any]] = {}
    for row in rows:
        deduped[tuple(row.get(c) for c in conflict_columns)] = row

    if len(deduped) < len(rows):
        log.warning(
            "upsert %s: collapsed %d row(s) duplicating %s within the batch",
            table,
            len(rows) - len(deduped),
            list(conflict_columns),
        )
    rows = list(deduped.values())

    columns = sorted({key for row in rows for key in row})

    col_sql = sql.SQL(", ").join(sql.Identifier(c) for c in columns)
    conflict_sql = sql.SQL(", ").join(sql.Identifier(c) for c in conflict_columns)

    if update_columns is None:
        update_columns = [c for c in columns if c not in set(conflict_columns)]

    if update_columns:
        assignments = sql.SQL(", ").join(
            sql.SQL("{col} = excluded.{col}").format(col=sql.Identifier(c))
            for c in update_columns
        )
        conflict_action = sql.SQL("do update set {assignments}").format(
            assignments=assignments
        )
    else:
        conflict_action = sql.SQL("do nothing")

    row_placeholder = sql.SQL("({})").format(
        sql.SQL(", ").join(sql.Placeholder() for _ in columns)
    )

    affected = 0
    with connect() as conn:
        for start in range(0, len(rows), batch_size):
            batch = rows[start : start + batch_size]
            values_sql = sql.SQL(", ").join(row_placeholder for _ in batch)
            statement = sql.SQL(
                "insert into {table} ({cols}) values {values} "
                "on conflict ({conflict}) {action}"
            ).format(
                table=sql.Identifier(table),
                cols=col_sql,
                values=values_sql,
                conflict=conflict_sql,
                action=conflict_action,
            )

            params: list[Any] = []
            for row in batch:
                params.extend(row.get(c) for c in columns)

            with conn.cursor() as cur:
                cur.execute(statement, params)
                affected += cur.rowcount

        conn.commit()

    return affected


def copy_into(
    table: str,
    columns: Sequence[str],
    rows: Iterable[Sequence[Any]],
) -> int:
    """Bulk-load via COPY. Returns rows written.

    COPY rather than multi-row INSERT because Phase 2c loads hundreds of
    thousands of rows: COPY streams them in one pass instead of building and
    parsing enormous statements.

    The tradeoff is that COPY has no ON CONFLICT, so this cannot be used where
    rows may already exist. It suits append-only tables (`plays`,
    `play_player_stats`) whose ingest is scoped to a season/week that is deleted
    and reloaded, which is why those loaders clear their slice first.
    """
    cols = sql.SQL(", ").join(sql.Identifier(c) for c in columns)
    statement = sql.SQL("copy {table} ({cols}) from stdin").format(
        table=sql.Identifier(table), cols=cols
    )

    written = 0
    with connect() as conn:
        with conn.cursor() as cur, cur.copy(statement) as copy:
            for row in rows:
                copy.write_row(row)
                written += 1
        conn.commit()
    return written


def execute(
    statement: str, params: tuple[Any, ...] | Mapping[str, Any] | None = None
) -> int:
    """Run a statement and return the affected row count."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute(statement, params)
        affected = cur.rowcount
        conn.commit()
        return affected


def database_size_mb() -> float:
    """Current database size in MB.

    The development project is on Supabase's free tier, which caps the database
    at 500 MB. Phase 2c loads several hundred thousand rows, so jobs check this
    before starting rather than discovering the ceiling with half a season
    loaded and no clean way to tell which half.
    """
    row = fetch_one("select pg_database_size(current_database()) as bytes")
    return (row["bytes"] / 1024 / 1024) if row else 0.0


def fetch_id_map(table: str, key_column: str, id_column: str = "id") -> dict[Any, int]:
    """Build {natural_key: surrogate_id} for resolving foreign keys.

    CFBD refers to entities by its own ids and by school name; the schema uses
    its own surrogate keys. Ingest resolves between them in Python rather than
    with correlated subqueries per row, which would turn one bulk insert into
    thousands of round trips.
    """
    rows = fetch_all(
        sql.SQL("select {key}, {id} from {table} where {key} is not null")
        .format(
            key=sql.Identifier(key_column),
            id=sql.Identifier(id_column),
            table=sql.Identifier(table),
        )
        .as_string(None)  # type: ignore[arg-type]
    )
    return {row[key_column]: row[id_column] for row in rows}


def count_rows(table: str) -> int:
    row = fetch_one(
        sql.SQL("select count(*) as n from {}")
        .format(sql.Identifier(table))
        .as_string(None)  # type: ignore[arg-type]
    )
    return int(row["n"]) if row else 0


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
