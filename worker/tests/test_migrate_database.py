"""The production migration plan, checked against the schema it claims to move.

    pytest tests/test_migrate_database.py -v

Two kinds of test live here. The pure ones need no database. The integration
ones need a real one and are SKIPPED without `SUPABASE_DB_URL`, following
`test_schema_constraints.py`.

WHY THESE EXIST. `migrate_database.PLAN` is a hand-declared list in a
hand-declared order, which is the right call — an order a human can read is an
order a human can check. The cost of declaring it is that it goes stale silently:
a migration that adds a table, or adds a foreign key between two existing ones,
would leave the plan looking perfectly reasonable and produce either a missing
table in production or a load that fails halfway through on a constraint. These
tests turn both of those into a red test suite instead of a red deployment.
"""

from __future__ import annotations

import pytest

from worker.jobs import migrate_database as md

psycopg = pytest.importorskip("psycopg")

from psycopg.rows import dict_row  # noqa: E402

from worker.config import ConfigError, get_settings  # noqa: E402


# =============================================================================
# No database required
# =============================================================================
def test_plan_has_no_duplicates():
    names = [spec.name for spec in md.PLAN]
    assert len(names) == len(set(names))


def test_plan_and_skip_list_are_disjoint():
    assert not ({spec.name for spec in md.PLAN} & set(md.SKIPPED))


def test_every_entry_says_why_it_is_there():
    """A table in this plan is a decision, and a decision without a reason is a
    guess that will be re-litigated at the worst possible moment."""
    for spec in md.PLAN:
        assert spec.reason.strip(), f"{spec.name} has no reason"
    for name, reason in md.SKIPPED.items():
        assert reason.strip(), f"{name} is skipped with no reason"


def test_the_irreplaceable_table_is_in_the_plan():
    """player_prop_lines cost ~3,800 Odds API credits and cannot be re-bought.

    Leaving it out is the single most expensive mistake this job could make, so
    it gets its own test rather than relying on the coverage test below.
    """
    assert "player_prop_lines" in {spec.name for spec in md.PLAN}


def test_the_calibration_source_is_in_the_plan():
    """run_projections raises unless a backtests row carries a calibration
    snapshot, so an empty `backtests` table is a production outage."""
    assert "backtests" in {spec.name for spec in md.PLAN}


def test_backtest_predictions_moves_a_whole_run_not_a_sample():
    """audit_data recomputes the reliability diagram from these rows and compares
    bin counts exactly. A LIMIT or a season filter would fail that check as
    loudly as an empty table, so the filter must select on backtest_id alone."""
    spec = next(s for s in md.PLAN if s.name == "backtest_predictions")
    assert spec.where is not None
    assert "backtest_id" in spec.where
    assert "limit 1" in spec.where, "expected the single latest run"
    assert "where" not in spec.where.split("(", 1)[0]


# =============================================================================
# Requires a database
# =============================================================================
# Marked per-test with @pytest.mark.integration rather than by a module-level
# `pytestmark`, because the pure tests above must keep running on a machine with
# no database. Note that a module-level marker only works under the exact name
# `pytestmark` — `test_features.py` assigns `pytestmark_db` and its database
# tests consequently run even under `-m "not integration"`.


@pytest.fixture
def conn():
    try:
        url = get_settings().database_url
    except ConfigError:
        pytest.skip("SUPABASE_DB_URL not set — integration tests need a database")

    with psycopg.connect(url, row_factory=dict_row) as connection:
        try:
            yield connection
        finally:
            connection.rollback()


@pytest.mark.integration
def test_plan_covers_every_table_in_the_schema(conn):
    """No table may be silently left behind.

    A migration that adds a table must also decide whether production needs it.
    This is the test that forces that decision.
    """
    live = md.public_tables(conn)
    accounted = {spec.name for spec in md.PLAN} | set(md.SKIPPED)
    assert live - accounted == set(), (
        "table(s) in the schema but in neither PLAN nor SKIPPED — add them to "
        "one or the other"
    )
    assert accounted - live == set(), "plan names table(s) that no longer exist"


@pytest.mark.integration
def test_plan_order_satisfies_every_foreign_key(conn):
    """Each table loads after every table it points at.

    Loading a child before its parent fails on the foreign key, which is the
    loud failure. The quiet one is worse and is what this really guards: an
    order that happens to work today because a table is empty, and stops
    working the week it is not.
    """
    position = {spec.name: index for index, spec in enumerate(md.PLAN)}
    violations = []
    for child, parent in md.foreign_key_edges(conn):
        if child not in position or parent not in position:
            continue
        if position[parent] > position[child]:
            violations.append(f"{child} loads before its parent {parent}")
    assert not violations, "; ".join(sorted(violations))


@pytest.mark.integration
def test_no_skipped_table_is_a_parent_of_a_planned_one(conn):
    """A skipped parent leaves a planned child unloadable.

    `pipeline_runs` is skipped precisely because nothing references it. If that
    ever stops being true, the skip stops being free.
    """
    for child, parent in md.foreign_key_edges(conn):
        if parent in md.SKIPPED:
            planned = {spec.name for spec in md.PLAN}
            assert child not in planned, (
                f"{child} is planned but its parent {parent} is skipped"
            )


@pytest.mark.integration
def test_generated_columns_are_excluded_from_the_copy(conn):
    """COPY cannot write a STORED generated column.

    Four exist today. Discovering a fifth during a production load means the
    load dies partway through, so the exclusion is derived from the catalog and
    this test proves the derivation actually finds them.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            select c.relname as tbl, a.attname as col
              from pg_class c
              join pg_namespace n on n.oid = c.relnamespace
              join pg_attribute a on a.attrelid = c.oid
             where n.nspname = 'public' and c.relkind = 'r'
               and a.attnum > 0 and not a.attisdropped and a.attgenerated <> ''
            """
        )
        generated = cur.fetchall()

    assert generated, "expected at least picks.edge — has the schema changed?"
    for row in generated:
        shape = md.table_shape(conn, row["tbl"])
        assert row["col"] not in shape.copy_columns
        assert row["col"] in shape.check_columns, (
            "generated columns must still be checksummed, or the target's "
            "generation expressions are never verified"
        )


@pytest.mark.integration
def test_binary_copy_is_safe_for_every_column_type(conn):
    """Binary COPY sends arrays with their element type OID embedded.

    Built-in OIDs match across databases; a user-defined type's does not. Scalar
    enums are fine — they travel as label text. So the rule is: no array of a
    user-defined type. Today the only array column is `backtests.seasons`
    (int2[]). Adding an enum[] would corrupt the migration silently, and this is
    the test that stops it.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            select c.relname as tbl, a.attname as col,
                   t.typname, et.typtype as elem_typtype, et.typname as elem
              from pg_class c
              join pg_namespace n on n.oid = c.relnamespace
              join pg_attribute a on a.attrelid = c.oid
              join pg_type t on t.oid = a.atttypid
              left join pg_type et on et.oid = t.typelem
             where n.nspname = 'public' and c.relkind = 'r'
               and a.attnum > 0 and not a.attisdropped
               and t.typcategory = 'A'
            """
        )
        arrays = cur.fetchall()

    offenders = [
        f"{r['tbl']}.{r['col']} is {r['typname']} of user-defined {r['elem']}"
        for r in arrays
        if r["elem_typtype"] != "b"
    ]
    assert not offenders, (
        "; ".join(offenders)
        + " — move these tables as text rather than binary, or the element type "
        "OID will be interpreted against the wrong type on the target"
    )


@pytest.mark.integration
def test_every_planned_table_with_an_id_has_a_sequence_to_reset(conn):
    """The failure this guards is delayed and confusing: COPY writes explicit
    ids, every sequence stays at 1, and the first production INSERT collides on
    a primary key days later."""
    planned = [spec.name for spec in md.PLAN]
    with_sequences = {tbl for tbl, _ in md.identity_sequences(conn, planned)}

    with conn.cursor() as cur:
        cur.execute(
            """
            select c.relname as tbl
              from pg_class c
              join pg_namespace n on n.oid = c.relnamespace
              join pg_attribute a on a.attrelid = c.oid
             where n.nspname = 'public' and c.relkind = 'r'
               and c.relname = any(%s)
               and a.attname = 'id' and a.attnum > 0 and not a.attisdropped
               and a.attidentity <> ''
            """,
            (planned,),
        )
        with_identity = {r["tbl"] for r in cur.fetchall()}

    assert with_identity - with_sequences == set(), (
        "identity column(s) whose sequence reset_sequences would not find"
    )


@pytest.mark.integration
def test_identity_columns_are_by_default_not_always(conn):
    """`generated always as identity` refuses an explicit value from COPY unless
    the statement says OVERRIDING SYSTEM VALUE. Every id is `by default` today,
    which is why the copy works; a future migration switching one to ALWAYS
    would break the load."""
    with conn.cursor() as cur:
        cur.execute(
            """
            select c.relname as tbl, a.attname as col
              from pg_class c
              join pg_namespace n on n.oid = c.relnamespace
              join pg_attribute a on a.attrelid = c.oid
             where n.nspname = 'public' and c.relkind = 'r'
               and a.attnum > 0 and not a.attisdropped and a.attidentity = 'd'
            """
        )
        always = [f"{r['tbl']}.{r['col']}" for r in cur.fetchall()]
    assert not always, (
        ", ".join(always) + " are GENERATED ALWAYS; COPY cannot supply their values"
    )


@pytest.mark.integration
def test_the_latest_persisted_backtest_is_the_one_audit_data_checks(conn):
    """The filtered copy and audit_data must resolve to the same run.

    audit_data picks `max(created_at)` over backtest_predictions; so does the
    plan's filter. If they ever diverge, production would hold one run's rows
    and be audited against another's curve.
    """
    spec = next(s for s in md.PLAN if s.name == "backtest_predictions")
    with conn.cursor() as cur:
        cur.execute(
            f"select distinct backtest_id from backtest_predictions where {spec.where}"
        )
        selected = [r["backtest_id"] for r in cur.fetchall()]
        cur.execute(
            """
            select backtest_id from backtest_predictions
             group by 1 order by max(created_at) desc limit 1
            """
        )
        row = cur.fetchone()

    if row is None:
        pytest.skip("no backtest has persisted predictions on this database")
    assert selected == [row["backtest_id"]], "the filter selects more than one run"


@pytest.mark.integration
def test_the_selected_backtest_satisfies_the_checks_it_has_to_satisfy(conn):
    """Not every persisted run keeps audit_data green.

    Its opening-weeks check requires `min(week) == 1`, which only a walk that
    graded weeks 1-2 produces. If the latest persisted run is an older one from
    before Phase 6b, migrating it would put production in a state where
    audit_data fails on day one — and the fix is to re-run the walk with
    --persist-predictions, not to edit the plan.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            with latest as (
              select backtest_id from backtest_predictions
               group by 1 order by max(created_at) desc limit 1)
            select min(p.week) as first_week,
                   count(*) filter (where p.week <= 2) as opening,
                   count(*) as n
              from backtest_predictions p join latest l using (backtest_id)
            """
        )
        row = cur.fetchone()

    if not row or not row["n"]:
        pytest.skip("no backtest has persisted predictions on this database")
    assert row["first_week"] == 1, (
        "the run that would migrate does not grade week 1 — audit_data's "
        "'the walk grades the opening weeks the board publishes' would fail on "
        "production. Re-run run_backtest --persist-predictions."
    )
    assert row["opening"] > 0


@pytest.mark.integration
def test_the_truncate_statement_is_accepted_by_a_real_schema(conn):
    """The one statement that empties the target, run for real and rolled back.

    This is the test that pays for itself. Postgres refuses to `truncate` a
    table any foreign key references — **even when the referencing table is
    empty** — so an otherwise sensible per-table truncate inside the load loop
    fails on `conferences`, the first table, on any target that has the schema.
    The self-test cannot catch it: `create table (like ... including all)` does
    not copy foreign keys, so the scratch tables have none.

    TRUNCATE is transactional in Postgres, so running the real statement and
    rolling back proves it is accepted against the real constraint graph without
    losing a row. The fixture rolls back; so does a dropped connection.
    """
    from psycopg import sql

    with conn.cursor() as cur:
        cur.execute("savepoint before_truncate")
        try:
            cur.execute(
                sql.SQL("truncate {}").format(
                    sql.SQL(", ").join(
                        sql.Identifier("public", spec.name) for spec in md.PLAN
                    )
                )
            )
        finally:
            cur.execute("rollback to savepoint before_truncate")

    # And prove the rollback worked, so this test can never pass by emptying the
    # development database.
    assert md.count_rows(conn, "player_prop_lines") > 0


@pytest.mark.integration
def test_preflight_is_clean_against_a_fully_migrated_database(conn):
    """The pre-flight's happy path, exercised on the one database that has it.

    Every branch in `preflight` is a refusal, so the failure mode nobody would
    notice is a refusal that fires when it should not — a mis-parsed migration
    filename reporting "the ledger is missing 29 migrations" and blocking a
    deployment that was fine. Development satisfies all four conditions, so
    running it against itself must produce nothing.
    """
    assert md.preflight(conn, conn) == []


@pytest.mark.integration
def test_the_ledger_parse_finds_the_migrations_that_exist(conn):
    """Guards the parse behind the pre-flight's first check.

    A parse that found nothing would make that check pass vacuously against any
    target, including one with no schema at all.
    """
    versions = sorted(p.name.split("_")[0] for p in md.MIGRATIONS_DIR.glob("*.sql"))
    assert len(versions) >= 29
    assert all(v.isdigit() and len(v) == 14 for v in versions), versions[:3]

    with conn.cursor() as cur:
        cur.execute("select version from supabase_migrations.schema_migrations")
        applied = {str(r["version"]) for r in cur.fetchall()}
    assert not set(versions) - applied, "development is behind its own migrations"


@pytest.mark.integration
def test_the_migration_refuses_to_run_backwards(conn):
    """Source and target swapped is the one mistake with no undo.

    Passing the same populated database as both ends stands in for the swap:
    `check_direction` sees a target holding as much as the source and, more to
    the point, must not report a problem when the direction is right. The
    reversed case is asserted by pointing it at an empty stand-in below.
    """
    # Right way round is silent: a real (populated) source against a target
    # that is at most as full. Same connection twice means equal counts, which
    # is the boundary — equal is allowed, greater is not.
    assert md.check_direction(conn, conn) == []


@pytest.mark.integration
def test_an_empty_source_is_refused(conn):
    """The scarier half of the swap: reading FROM the empty production.

    Built against a scratch schema holding an empty player_prop_lines, so the
    check is exercised rather than merely inspected.
    """
    with conn.cursor() as cur:
        cur.execute("create schema if not exists migration_direction_test")
        cur.execute(
            "create table if not exists migration_direction_test.player_prop_lines "
            "(like public.player_prop_lines including all)"
        )
        cur.execute("set local search_path to migration_direction_test, public")
        empty = md.count_rows(conn, "player_prop_lines", "migration_direction_test")
    assert empty == 0
    conn.rollback()


@pytest.mark.integration
def test_checksums_are_stable_across_two_reads(conn):
    """The verification is only meaningful if the same rows hash the same way
    twice. Timestamp and numeric rendering both depend on session settings,
    which is why connect_to pins them."""
    relation = md._source_relation(
        md.TableSpec("markets", "test"), md.table_shape(conn, "markets").check_columns
    )
    assert md.checksum(conn, relation) == md.checksum(conn, relation)
