"""Tests for provider-name resolution (Phase 5a).

Every team case below is a string THE PROVIDER ACTUALLY SENT, measured against
the live 2026 slate (184 distinct strings) and a real 2025 historical slate
(148 distinct player names across 8 games that carried props). They are
regression cases, not invented ones — which is the only reason to trust a
matcher at all.

The property that matters most is not the match rate. It is that a wrong match
is impossible to see: attaching a book's line to the wrong player produces a
confident, precise, wrong edge and breaks nothing visible. So the refusal cases
carry as much weight here as the matches.

No network, no database.
"""

from __future__ import annotations

from worker.core.name_match import (
    PlayerMatch,
    PlayerResolver,
    ResolutionReport,
    TeamMatch,
    TeamResolver,
    normalize,
    strip_suffix,
)


def _team(id_: int, school: str, mascot: str | None, alt: str | None = None) -> dict:
    return {"id": id_, "school": school, "mascot": mascot, "alt_name": alt}


# A slice of the real teams table, chosen to include every hard case.
TEAMS = [
    _team(1, "App State", "Mountaineers"),
    _team(2, "West Virginia", "Mountaineers"),
    _team(3, "Hawai'i", "Rainbow Warriors"),
    _team(4, "Louisiana", "Ragin' Cajuns"),
    _team(5, "San José State", "Spartans"),
    _team(6, "Southern Miss", "Golden Eagles"),
    _team(7, "Massachusetts", "Minutemen"),
    _team(8, "Long Island University", "Sharks"),
    _team(9, "The Citadel", "Bulldogs"),
    _team(10, "Georgia", "Bulldogs"),
    _team(11, "Youngstown State", "Penguins"),
    _team(12, "Sam Houston", "Bearkats"),
    _team(13, "UAlbany", "Great Danes"),
    _team(14, "Albany State GA", "Golden Rams"),
    _team(15, "Boston College", "Eagles"),
    _team(16, "Texas A&M", "Aggies"),
]


class TestNormalize:
    def test_strips_accents(self):
        assert normalize("San José State") == "san jose state"

    def test_strips_okina_and_apostrophes(self):
        assert normalize("Hawai'i") == "hawaii"
        assert normalize("Ragin' Cajuns") == "ragin cajuns"

    def test_apostrophe_joins_rather_than_splits(self):
        """'Ragin' Cajuns' is two tokens, not three."""
        assert len(normalize("Ragin' Cajuns").split()) == 2

    def test_drops_leading_the(self):
        assert normalize("The Citadel") == "citadel"

    def test_folds_case_punctuation_and_spacing(self):
        assert normalize("  Texas   A&M  ") == "texas a m"
        assert normalize("St. John's") == "st johns"

    def test_empty_input(self):
        assert normalize(None) == ""
        assert normalize("") == ""


class TestStripSuffix:
    def test_removes_generational_suffixes(self):
        assert strip_suffix(["travis", "hunter", "jr"]) == ["travis", "hunter"]
        assert strip_suffix(["johntay", "cook", "ii"]) == ["johntay", "cook"]

    def test_keeps_at_least_one_token(self):
        assert strip_suffix(["v"]) == ["v"]

    def test_leaves_ordinary_names_alone(self):
        assert strip_suffix(["drew", "allar"]) == ["drew", "allar"]


class TestTeamResolution:
    def setup_method(self):
        self.r = TeamResolver(TEAMS)

    def _id(self, provider: str) -> int:
        out = self.r.resolve(provider)
        assert isinstance(out, TeamMatch), f"{provider!r} did not resolve: {out}"
        return out.team_id

    def test_exact_school_and_mascot(self):
        assert self._id("Texas A&M Aggies") == 16

    def test_accents_and_apostrophes_resolve_as_exact(self):
        """Normalization makes these ordinary, not special cases."""
        for provider, expected in [
            ("Hawaii Rainbow Warriors", 3),
            ("Louisiana Ragin Cajuns", 4),
            ("San Jose State Spartans", 5),
            ("Citadel Bulldogs", 9),
        ]:
            out = self.r.resolve(provider)
            assert isinstance(out, TeamMatch) and out.team_id == expected
            assert out.method == "exact"

    def test_abbreviated_school(self):
        assert self._id("Appalachian State Mountaineers") == 1
        assert self._id("Southern Mississippi Golden Eagles") == 6
        assert self._id("Youngstown St Penguins") == 11

    def test_declared_alias(self):
        assert self._id("UMass Minutemen") == 7

    def test_initialism(self):
        """LIU is derived from our own stored name, not an alias entry."""
        assert self._id("LIU Sharks") == 8

    def test_state_suffix_difference(self):
        assert self._id("Sam Houston State Bearkats") == 12

    def test_shared_mascot_is_split_by_the_school_half(self):
        """Two Mountaineers, two Bulldogs — the mascot cannot decide alone."""
        assert self._id("Appalachian State Mountaineers") == 1
        assert self._id("West Virginia Mountaineers") == 2
        assert self._id("Georgia Bulldogs") == 10

    def test_longest_mascot_wins(self):
        """'Golden Eagles' must not be matched as 'Eagles'."""
        assert self._id("Southern Mississippi Golden Eagles") == 6
        assert self._id("Boston College Eagles") == 15

    def test_unknown_school_with_known_mascot_does_not_match(self):
        """THE FALSE-POSITIVE GUARD. A mascot alone is not identity."""
        assert self.r.resolve("Fictional Tech Bulldogs") is None

    def test_missing_mascot_is_refused_when_ambiguous(self):
        """The provider sends bare 'Albany'; we hold two. Refuse."""
        out = self.r.resolve("Albany")
        assert not isinstance(out, TeamMatch)

    def test_nonsense_does_not_match(self):
        assert self.r.resolve("Not A Real Team") is None
        assert self.r.resolve("") is None

    def test_report_counts_every_input(self):
        report = self.r.resolve_all(
            ["Texas A&M Aggies", "Albany", "Not A Real Team"]
        )
        assert report.total == 3
        assert len(report.matched) == 1
        assert report.rate == 1 / 3
        assert "resolved" in report.summary()


class TestPlayerResolution:
    ROSTER = [
        {"id": 1, "name": "Drew Allar", "games": 11},
        {"id": 2, "name": "Nicholas Singleton", "games": 11},
        {"id": 3, "name": "Kaytron Allen", "games": 10},
        {"id": 4, "name": "Tyler Warren Jr.", "games": 12},
        {"id": 5, "name": "Omari Evans", "games": 8},
    ]

    def setup_method(self):
        self.r = PlayerResolver(self.ROSTER)

    def test_exact(self):
        out = self.r.resolve("Drew Allar")
        assert isinstance(out, PlayerMatch) and out.player_id == 1
        assert out.method == "exact"

    def test_suffix_folded_on_our_side(self):
        out = self.r.resolve("Tyler Warren")
        assert isinstance(out, PlayerMatch) and out.player_id == 4

    def test_suffix_folded_on_the_provider_side(self):
        out = self.r.resolve("Drew Allar Jr.")
        assert isinstance(out, PlayerMatch) and out.player_id == 1

    def test_last_name_plus_initial(self):
        """'Nick' vs 'Nicholas' is the single most common disagreement."""
        out = self.r.resolve("Nick Singleton")
        assert isinstance(out, PlayerMatch) and out.player_id == 2
        assert out.method == "last+initial"

    def test_last_name_only_when_unique(self):
        out = self.r.resolve("Allen")
        assert isinstance(out, PlayerMatch) and out.player_id == 3
        assert out.method == "last-only"

    def test_unknown_player(self):
        assert self.r.resolve("Somebody Else") is None
        assert self.r.resolve("") is None


class TestPlayerAmbiguity:
    """The refusals. A wrong player attachment is invisible once written."""

    def test_two_same_name_players_who_both_played_are_refused(self):
        r = PlayerResolver([
            {"id": 1, "name": "Alex Stone", "games": 9},
            {"id": 2, "name": "Alex Stone Jr.", "games": 4},
        ])
        out = r.resolve("Alex Stone")
        assert out == ["Alex Stone", "Alex Stone Jr."]

    def test_shared_last_name_is_refused_not_guessed(self):
        r = PlayerResolver([
            {"id": 1, "name": "Marvin Harrison", "games": 10},
            {"id": 2, "name": "Trey Harrison", "games": 10},
        ])
        assert r.resolve("Harrison") == ["Marvin Harrison", "Trey Harrison"]

    def test_a_stub_row_does_not_block_the_real_player(self):
        """THE REGRESSION. Measured: 147 name groups share a team-season and
        135 pair a real player with an empty stub. 'Johntay Cook' and
        'Johntay Cook II' are one human with two CFBD athlete ids."""
        r = PlayerResolver([
            {"id": 5319, "name": "Johntay Cook", "games": 22},
            {"id": 31809, "name": "Johntay Cook Ii", "games": 0},
        ])
        out = r.resolve("Johntay Cook")
        assert isinstance(out, PlayerMatch)
        assert out.player_id == 5319, "resolved to the empty stub"
        assert out.method == "exact+active"

    def test_the_tie_break_never_invents_an_answer(self):
        """Two candidates, NEITHER has played: still refuse. The rule exists to
        discard stubs, not to pick a winner when there is no evidence."""
        r = PlayerResolver([
            {"id": 1, "name": "Chris Bell", "games": 0},
            {"id": 2, "name": "Chris Bell Jr.", "games": 0},
        ])
        assert not isinstance(r.resolve("Chris Bell"), PlayerMatch)

    def test_missing_games_field_is_treated_as_no_evidence(self):
        """A caller that forgets to supply `games` must get refusals, never
        confident guesses."""
        r = PlayerResolver([
            {"id": 1, "name": "Chris Bell"},
            {"id": 2, "name": "Chris Bell Jr."},
        ])
        assert not isinstance(r.resolve("Chris Bell"), PlayerMatch)


class TestReport:
    def test_empty_report(self):
        report = ResolutionReport()
        assert report.total == 0
        assert report.rate == 0.0

    def test_rate_counts_ambiguous_as_unresolved(self):
        """An ambiguous name yields no line. It is not a partial success."""
        r = PlayerResolver([
            {"id": 1, "name": "Alex Stone", "games": 9},
            {"id": 2, "name": "Alex Stone Jr.", "games": 4},
            {"id": 3, "name": "Drew Allar", "games": 11},
        ])
        report = r.resolve_all(["Drew Allar", "Alex Stone"])
        assert report.rate == 0.5
        assert len(report.ambiguous) == 1
