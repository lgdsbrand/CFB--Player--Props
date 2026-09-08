"""`audit_data`'s own checks have to ask their question per sport.

THE CANARY IS THE THING THAT BROKE. From 2026-09-06 — the day NFL projections
first landed on production — the check "v_slate_weeks counts agree with the
tables they summarise" failed every single day on data that was entirely
correct. `v_slate_weeks` groups by (season, week, sport); the cross-check
counted `projections` by (season, week) alone, so it compared one sport's row
against both sports' rows and called two weeks wrong.

Measured on production 2026-09-08: the sport-blind query returned `wrong=2`, the
sport-aware one `wrong=0`, over the same 19 rows.

That is worse than a check nobody wrote. `audit_data` exits non-zero when ANY
check fails, so one false alarm makes the whole data-integrity canary red and
hides whatever else it would have caught — and the monitor's daily "audit_data
last run FAILED" then reads as noise.

**(season, week) is not a key in this database. (season, week, sport) is.** This
is the sixth place that has had to be fixed and the second inside `audit_data`.

Source-level and offline: the checks in that module run at import time and need
a database, so the file is read as text rather than imported.
"""

from __future__ import annotations

import re
from pathlib import Path

AUDIT = Path(__file__).resolve().parents[1] / "worker" / "jobs" / "audit_data.py"


def _check_bodies() -> list[tuple[str, str]]:
    """[(check name, its SQL)] for every `check(G, "...", \"\"\"...\"\"\", ...)`."""
    source = AUDIT.read_text(encoding="utf-8")
    found = re.findall(
        r'check\(\s*G,\s*"([^"]+)",\s*"""(.*?)"""', source, flags=re.DOTALL
    )
    return [(name, sql) for name, sql in found]


def test_the_check_parser_actually_finds_checks() -> None:
    # Guards the guard: a regex that stops matching would make the assertion
    # below pass against an empty list, which is the failure mode these
    # source-reading tests exist to prevent.
    bodies = _check_bodies()
    assert len(bodies) > 100, len(bodies)
    assert any("v_slate_weeks" in sql for _, sql in bodies)


def test_every_check_that_reads_a_sport_split_view_constrains_sport() -> None:
    """A view grouped by sport, cross-checked without one, compares both sports.

    Listed by view rather than inferred from column names because the mistake is
    invisible in the SQL: the sport-blind version reads perfectly, joins
    correctly, and returns a number — just not the number it claims to.
    """
    sport_split_views = ("v_slate_weeks",)

    offenders = []
    for name, sql in _check_bodies():
        for view in sport_split_views:
            if view in sql and "sport" not in sql:
                offenders.append(f"{name} (reads {view})")

    assert not offenders, (
        "audit checks read a sport-split view without constraining sport: "
        f"{offenders} — these compare one sport's row against both sports' "
        "rows and fail on correct data, which turns the whole canary red"
    )
