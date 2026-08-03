"""Tests for the CFBD -> schema translation layer.

Position normalization gets the most attention here because it is the one that
fails silently. A mis-bucketed position does not raise; it moves a player's
production into the wrong defensive split, which surfaces much later as a
projection that is subtly wrong for reasons nobody can trace.

No network and no database.
"""

from __future__ import annotations

import pytest

from worker.adapters.cfbd.mapping import (
    POSITION_MAP,
    POSTSEASON_WEEK_OFFSET,
    SKILL_POSITIONS,
    VALID_POSITION_GROUPS,
    PositionNormalizer,
    bigint_or_none,
    inches_or_none,
    normalize_classification,
    normalize_season_type,
    pounds_or_none,
    smallint_or_none,
    week_for_api,
    week_on_season_axis,
)

# ------------------------------------------------------------------- positions


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("QB", "QB"),
        ("RB", "RB"),
        ("WR", "WR"),
        ("TE", "TE"),
        # Offensive line variants all collapse to OL.
        ("OT", "OL"), ("G", "OL"), ("C", "OL"), ("OL", "OL"),
        # Defensive front variants collapse to DL.
        ("DE", "DL"), ("DT", "DL"), ("NT", "DL"), ("EDGE", "DL"),
        # Secondary.
        ("CB", "DB"), ("S", "DB"), ("FS", "DB"), ("SS", "DB"),
        # Specialists.
        ("PK", "K"), ("K", "K"), ("P", "P"),
    ],
)
def test_known_positions_map_correctly(raw, expected):
    assert PositionNormalizer().normalize(raw) == expected


def test_fullback_counts_as_a_running_back():
    """FB takes goal-line carries and short receptions.

    Bucketing it as OTHER would drop that production out of 'rush yards allowed
    to RBs', which is the exact number the RB market is graded against.
    """
    assert PositionNormalizer().normalize("FB") == "RB"


def test_normalization_is_case_and_whitespace_insensitive():
    n = PositionNormalizer()
    assert n.normalize(" qb ") == "QB"
    assert n.normalize("Wr") == "WR"


@pytest.mark.parametrize("raw", [None, "", "LS", "ATH", "?", "SNORKELER"])
def test_unknown_positions_fall_back_to_other(raw):
    assert PositionNormalizer().normalize(raw) == "OTHER"


def test_unmapped_positions_are_counted_not_swallowed():
    """A new CFBD position string must be visible, not silently OTHER."""
    n = PositionNormalizer()
    n.normalize("QB")
    n.normalize("NEWPOS")
    n.normalize("NEWPOS")
    n.normalize(None)

    assert n.mapped["QB"] == 1
    assert n.unmapped["NEWPOS"] == 2
    assert n.unmapped["<missing>"] == 1


def test_every_mapped_target_is_a_valid_enum_value():
    """A typo in POSITION_MAP would fail at INSERT time, mid-backfill."""
    assert set(POSITION_MAP.values()) <= VALID_POSITION_GROUPS


def test_skill_positions_are_all_mappable():
    """Every position we model markets for must be reachable from the map."""
    assert SKILL_POSITIONS <= set(POSITION_MAP.values())


# -------------------------------------------------------------- classification


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("fbs", "fbs"), ("FBS", "fbs"), ("fcs", "fcs"), ("ii", "ii"), ("iii", "iii")],
)
def test_classification_mapping(raw, expected):
    assert normalize_classification(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "nonsense"])
def test_unknown_classification_is_other(raw):
    assert normalize_classification(raw) == "other"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("regular", "regular"), ("postseason", "postseason"), (None, "regular"),
     ("REGULAR", "regular"), ("something", "regular")],
)
def test_season_type_mapping(raw, expected):
    assert normalize_season_type(raw) == expected


# ------------------------------------------------------------------- coercions


def test_bigint_accepts_the_string_ids_roster_returns():
    """The bug this exists for: /roster sends athlete ids as strings."""
    assert bigint_or_none("102597") == 102597
    assert bigint_or_none(102597) == 102597
    assert bigint_or_none(None) is None
    assert bigint_or_none("") is None
    assert bigint_or_none("not-a-number") is None


def test_height_rejects_implausible_values():
    assert inches_or_none(74) == 74
    assert inches_or_none("74") == 74
    assert inches_or_none(2) is None      # junk
    assert inches_or_none(300) is None    # junk
    assert inches_or_none(None) is None


def test_weight_rejects_implausible_values():
    assert pounds_or_none(216) == 216
    assert pounds_or_none(5) is None
    assert pounds_or_none(9999) is None


def test_smallint_guards_postgres_range():
    """An out-of-range value must become NULL, not abort the whole batch."""
    assert smallint_or_none(15) == 15
    assert smallint_or_none(32768) is None
    assert smallint_or_none(-32769) is None
    assert smallint_or_none("12") == 12
    assert smallint_or_none(None) is None


# ----------------------------------------------------------------- season axis
def test_regular_weeks_are_stored_as_the_source_numbers_them():
    assert week_on_season_axis(1, "regular") == 1
    assert week_on_season_axis(16, "regular") == 16


def test_postseason_weeks_are_pushed_past_any_regular_season():
    """The bug this exists to prevent.

    CFBD restarts week numbering at 1 for the postseason, so a bowl played in
    December arrived labelled the same as the season opener. `games.week` is the
    time axis every point-in-time cutoff is defined against, so a December
    result sitting in week 1 leaked into every "through week N" aggregation for
    N >= 2 — the disqualifying lookahead of CLAUDE.md §4.
    """
    assert week_on_season_axis(1, "postseason") == 21
    assert week_on_season_axis(2, "postseason") == 22


def test_no_postseason_week_can_collide_with_a_regular_one():
    """The offset must clear the longest regular season, with room to spare."""
    regular = {week_on_season_axis(w, "regular") for w in range(1, 20)}
    postseason = {week_on_season_axis(w, "postseason") for w in range(1, 6)}
    assert regular.isdisjoint(postseason)
    assert min(postseason) > POSTSEASON_WEEK_OFFSET


def test_the_api_week_round_trips():
    """Per-week endpoints are driven from our own games table.

    Without the inverse the stats ingest asks CFBD for postseason week 21 and
    gets an empty list back — no error, just a season quietly missing its bowl
    games.
    """
    for season_type in ("regular", "postseason"):
        for week in range(1, 17):
            stored = week_on_season_axis(week, season_type)
            assert week_for_api(stored, season_type) == week
