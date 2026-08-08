"""Tests that keep the docs true (Phase 5e).

Documentation is the one artefact in this repo with no failure mode. Code that
names a renamed job crashes; a runbook that names a renamed job reads perfectly
and sends whoever is following it down a hole at the exact moment they are least
able to afford one. Nothing else in the suite can notice.

So the three things a reader would act on are checked mechanically:

**Every job is documented and every documented job exists.** `docs/runbook.md`
against `worker/jobs/*.py` and against the crons in `render.yaml`.

**Every configuration key is documented and every documented key exists.**
`docs/configuration.md` against the `insert into app_config` statements in the
migrations, which are the source of truth for what the table holds.

**Every adapter name quoted in the docs is one the registry knows.** These are
copied by hand into SQL by whoever switches a seam on — `theoddsapi` was written
as `the_odds_api` in the first draft of `docs/odds.md`, which would have raised
`OddsAdapterError` for someone following the instructions exactly.

This is the same guard as `tests/test_monitor.py`, which pins `MONITORED_JOBS` to
`render.yaml`, and it is here for the same reason: neither side can tell it has
fallen out of step with the other.

All offline. No database, no network.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from worker.adapters.ai import KNOWN_ADAPTERS as KNOWN_AI_ADAPTERS
from worker.adapters.alerts import KNOWN_ADAPTERS as KNOWN_ALERT_ADAPTERS
from worker.adapters.odds import KNOWN_ADAPTERS as KNOWN_ODDS_ADAPTERS

REPO_ROOT = Path(__file__).resolve().parents[2]

RUNBOOK = REPO_ROOT / "docs" / "runbook.md"
CONFIG_DOC = REPO_ROOT / "docs" / "configuration.md"
ODDS_DOC = REPO_ROOT / "docs" / "odds.md"
ENV_EXAMPLE = REPO_ROOT / ".env.example"
RENDER_YAML = REPO_ROOT / "render.yaml"
JOBS_DIR = REPO_ROOT / "worker" / "worker" / "jobs"
MIGRATIONS_DIR = REPO_ROOT / "supabase" / "migrations"

# Jobs the runbook documents as deliberately unscheduled. Listed here rather
# than inferred so that scheduling one without revisiting the prose fails: the
# runbook says "neither of these is scheduled, and neither should be", which
# stops being true the moment a cron appears.
RUN_BY_HAND = frozenset({"run_backtest", "probe_odds", "migrate_database"})


# -----------------------------------------------------------------------------
# Sources of truth
# -----------------------------------------------------------------------------
def _job_modules() -> set[str]:
    """Every runnable `python -m worker.jobs.<name>` module."""
    return {p.stem for p in JOBS_DIR.glob("*.py") if p.stem != "__init__"}


def _scheduled_jobs() -> set[str]:
    """Job modules named by a cron `startCommand` in render.yaml.

    Regex rather than a YAML library for the reason given in test_monitor.py:
    PyYAML is not a dependency and CLAUDE.md §0 requires justifying a new one.
    """
    text = RENDER_YAML.read_text(encoding="utf-8")
    blocks = text.split("- type: cron")[1:]
    return {
        module for block in blocks for module in re.findall(r"python -m worker\.jobs\.(\w+)", block)
    }


def _documented_jobs(doc: Path) -> set[str]:
    """Job modules a doc tells the reader to run."""
    text = doc.read_text(encoding="utf-8")
    return set(re.findall(r"python -m worker\.jobs\.(\w+)", text))


def _config_keys_from_migrations() -> set[str]:
    """Keys inserted into app_config by any migration.

    Split on a semicolon at end of line rather than any semicolon: several
    descriptions contain one mid-sentence, and splitting on those truncated the
    statement and silently found a fraction of the keys. The count assertion in
    `test_config_key_extraction_actually_works` is what caught that.
    """
    keys: set[str] = set()
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        text = path.read_text(encoding="utf-8")
        for tail in re.split(r"insert\s+into\s+app_config", text, flags=re.I)[1:]:
            statement = re.split(r";\s*$", tail, maxsplit=1, flags=re.M)[0]
            keys.update(re.findall(r"^\s*\('([a-z_]+)',", statement, re.M))
    return keys


def _documented_config_keys() -> set[str]:
    """Keys documented in configuration.md.

    Two shapes are accepted, because the doc uses both: a `#### `key`` heading
    for the adapter seams that need prose, and a leading `| `key` |` table cell
    for the ones a row describes adequately.

    The heading match is pinned to four hashes rather than `#+`. The `##
    app_config` section title is also a backticked identifier at the start of a
    heading, and the looser pattern read the table's own name as a key in it.
    """
    text = CONFIG_DOC.read_text(encoding="utf-8")
    headings = re.findall(r"^####\s+`([a-z_]+)`", text, re.M)
    cells = re.findall(r"^\|\s*`([a-z_]+)`\s*\|", text, re.M)
    return set(headings) | set(cells)


def _known_values(doc: Path, key: str) -> set[str]:
    """Adapter names the doc lists under a `#### \\`key\\`` heading.

    Reads only as far as the next heading, so a `Known values:` line belonging
    to a different seam cannot be attributed to this one — and then to the end
    of the paragraph rather than the end of the line, because the list wraps
    when the names are long. Stopping at the newline found one of three.
    """
    text = doc.read_text(encoding="utf-8")
    match = re.search(rf"^#+\s+`{key}`.*?$(.*?)(?=^#|\Z)", text, re.M | re.S)
    assert match, f"{doc.name} has no section for {key!r}"
    values = re.search(r"Known values:\s*(.+?)(?=\n\s*\n|\Z)", match.group(1), re.S)
    assert values, f"{doc.name} section for {key!r} does not list its known values"
    return set(re.findall(r'`"([a-z_]+)"`', values.group(1)))


# -----------------------------------------------------------------------------
# Guards on the guards
# -----------------------------------------------------------------------------
# Every assertion below compares two parsed sets. A parse that silently finds
# nothing would make all of them pass vacuously, which is the failure mode this
# whole file exists to prevent — so each parser is pinned to a known member and
# a floor on its size first.
def test_job_module_discovery_actually_works() -> None:
    modules = _job_modules()
    assert len(modules) >= 12, modules
    assert {"healthcheck", "run_projections", "audit_data"} <= modules


def test_runbook_parse_actually_works() -> None:
    documented = _documented_jobs(RUNBOOK)
    assert len(documented) >= 12, documented
    assert "healthcheck" in documented


def test_config_key_extraction_actually_works() -> None:
    keys = _config_keys_from_migrations()
    assert len(keys) >= 15, keys
    # One key per seam, one threshold, one from the newest migration — a parser
    # that stops after the first tuple of a multi-row insert passes the count
    # but fails these.
    assert {"odds_adapter", "ai_adapter", "alert_adapter", "edge_threshold"} <= keys


def test_documented_config_key_extraction_actually_works() -> None:
    keys = _documented_config_keys()
    assert len(keys) >= 15, keys
    assert "hit_rate_windows" in keys


# -----------------------------------------------------------------------------
# docs/runbook.md <-> the jobs that exist
# -----------------------------------------------------------------------------
def test_every_job_is_in_the_runbook() -> None:
    missing = _job_modules() - _documented_jobs(RUNBOOK)
    assert not missing, (
        f"jobs with no runbook entry: {sorted(missing)} — "
        "nobody on call knows these exist or what they write"
    )


def test_runbook_names_no_job_that_does_not_exist() -> None:
    phantom = _documented_jobs(RUNBOOK) - _job_modules()
    assert not phantom, (
        f"runbook documents jobs that are not there: {sorted(phantom)} — "
        "following it would produce ModuleNotFoundError at the worst moment"
    )


def test_every_scheduled_job_is_in_the_runbook() -> None:
    missing = _scheduled_jobs() - _documented_jobs(RUNBOOK)
    assert not missing, (
        f"scheduled in render.yaml but absent from the runbook: {sorted(missing)} — "
        "something runs in production that the operations doc does not mention"
    )


def test_jobs_documented_as_manual_are_not_scheduled() -> None:
    # The runbook states these are run by hand. Scheduling one without changing
    # that sentence leaves a doc that is confidently wrong about production.
    contradicted = RUN_BY_HAND & _scheduled_jobs()
    assert not contradicted, (
        f"runbook says these are run by hand, render.yaml schedules them: {sorted(contradicted)}"
    )


def test_manual_jobs_are_real_jobs() -> None:
    # Guards RUN_BY_HAND itself: a renamed job would leave this set naming
    # nothing, and test_jobs_documented_as_manual_are_not_scheduled would then
    # pass for the wrong reason.
    assert RUN_BY_HAND <= _job_modules()


# -----------------------------------------------------------------------------
# docs/configuration.md <-> app_config
# -----------------------------------------------------------------------------
def test_every_config_key_is_documented() -> None:
    missing = _config_keys_from_migrations() - _documented_config_keys()
    assert not missing, (
        f"app_config keys with no entry in configuration.md: {sorted(missing)} — "
        "a knob nobody knows about is a knob nobody turns back"
    )


def test_configuration_doc_invents_no_keys() -> None:
    phantom = _documented_config_keys() - _config_keys_from_migrations()
    assert not phantom, (
        f"configuration.md documents keys no migration inserts: {sorted(phantom)} — "
        "an `update app_config` against one of these silently affects nothing"
    )


# -----------------------------------------------------------------------------
# Adapter names quoted in the docs
# -----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("key", "known"),
    [
        ("odds_adapter", KNOWN_ODDS_ADAPTERS),
        ("ai_adapter", KNOWN_AI_ADAPTERS),
        ("alert_adapter", KNOWN_ALERT_ADAPTERS),
    ],
)
def test_documented_adapter_names_match_the_registry(key: str, known: tuple[str, ...]) -> None:
    documented = _known_values(CONFIG_DOC, key)
    assert documented == set(known), (
        f"configuration.md lists {sorted(documented)} for {key}, registry knows "
        f"{sorted(known)} — every one of these gets hand-copied into a SQL "
        "update, and get_adapter raises on an unknown name rather than falling "
        "back"
    )


def test_odds_doc_switch_on_instruction_uses_a_real_adapter_name() -> None:
    # docs/odds.md carries a copy-pasteable `update app_config` for turning the
    # odds seam on. This is the exact line a reader runs, so it is checked
    # against the registry rather than against the other doc.
    text = ODDS_DOC.read_text(encoding="utf-8")
    statements = re.findall(
        r"update app_config set value = '\"([a-z_]+)\"'::jsonb\s+where key = 'odds_adapter'",
        text,
    )
    assert statements, "odds.md no longer shows how to switch the odds adapter on"
    unknown = set(statements) - set(KNOWN_ODDS_ADAPTERS)
    assert not unknown, (
        f"odds.md tells the reader to set odds_adapter to {sorted(unknown)}, "
        f"which get_adapter would reject. Known: {sorted(KNOWN_ODDS_ADAPTERS)}"
    )


# -----------------------------------------------------------------------------
# Environment variables
# -----------------------------------------------------------------------------
def test_documented_env_vars_exist_in_the_template() -> None:
    """Every variable in configuration.md's table is in .env.example.

    The table is an index; .env.example is where the reasoning lives and what
    gets copied to .env. A name that appears only in the index is one nobody
    can actually set, because they set it by copying the template.
    """
    documented = set(
        re.findall(r"^\|\s*`([A-Z][A-Z0-9_]+)`\s*\|", CONFIG_DOC.read_text(encoding="utf-8"), re.M)
    )
    assert len(documented) >= 13, documented

    template = set(
        re.findall(r"^([A-Z][A-Z0-9_]+)=", ENV_EXAMPLE.read_text(encoding="utf-8"), re.M)
    )
    assert len(template) >= 13, template

    assert documented == template, (
        "configuration.md and .env.example disagree about the environment. "
        f"documented but not in the template: {sorted(documented - template)}; "
        f"in the template but undocumented: {sorted(template - documented)}"
    )
