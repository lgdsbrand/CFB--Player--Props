"""Tests for the opponent-adjustment fit.

The point of the adjustment is that a defense which happened to face strong
offences should not be judged as though it were bad. That is a property with a
known answer on synthetic data, so it is tested directly rather than eyeballed
on real standings — on real data a wrong adjustment still produces a plausible
looking table.

No network and no database.
"""

from __future__ import annotations

from worker.core.splits import (
    ADJUSTMENT_VERSION,
    RANK_METRICS,
    SHRINKAGE_GAMES,
    SKILL_POSITIONS,
    Observation,
    _fit_additive,
)

METRIC = "rush_yards_allowed"


def obs(defense, offense, value, week=1):
    return Observation(
        defense_id=defense, offense_id=offense, week=week, values={METRIC: value}
    )


def test_league_mean_is_recovered():
    observations = [obs(1, 10, 100), obs(2, 11, 120), obs(3, 12, 80)]
    mean, _, _ = _fit_additive(observations, METRIC)
    assert mean == 100.0


def test_defense_effects_recovered_on_a_balanced_schedule():
    """With every defense facing the same offences, effects are just deviations."""
    observations = []
    for offense in (10, 11, 12):
        observations.append(obs(1, offense, 120))   # +20 defense
        observations.append(obs(2, offense, 100))   # league average
        observations.append(obs(3, offense, 80))    # -20 defense
    mean, defense, _ = _fit_additive(observations, METRIC)

    assert mean == 100.0
    assert defense[1] == 20.0
    assert defense[2] == 0.0
    assert defense[3] == -20.0


def test_unbalanced_schedule_is_corrected():
    """The whole reason the adjustment exists.

    Truth: every defense here is league-average; offences S are +50 and W are
    -50. Defense 1 draws mostly S and so ALLOWS more; defense 2 draws mostly W.
    Raw per-game averages (125 vs 75) say one is far better than the other. The
    fit should conclude they are equivalent and attribute the gap to the
    offences.

    The schedules overlap (both face S1 and W1), which is what makes the two
    effects separable at all — see the disconnected case below.
    """
    s1, s2, w1, w2 = 10, 11, 20, 21
    observations = [
        # defense 1: three strong offences, one weak
        obs(1, s1, 150), obs(1, s2, 150), obs(1, s1, 150), obs(1, w1, 50),
        # defense 2: three weak offences, one strong
        obs(2, w1, 50), obs(2, w2, 50), obs(2, w1, 50), obs(2, s1, 150),
        # two more defenses to tie the graph together
        obs(3, s1, 150), obs(3, w1, 50), obs(3, s2, 150), obs(3, w2, 50),
        obs(4, s2, 150), obs(4, w2, 50), obs(4, s1, 150), obs(4, w1, 50),
    ]

    raw_1 = sum(o.values[METRIC] for o in observations if o.defense_id == 1) / 4
    raw_2 = sum(o.values[METRIC] for o in observations if o.defense_id == 2) / 4
    assert raw_1 == 125.0 and raw_2 == 75.0  # raw numbers disagree sharply

    _, defense, offense = _fit_additive(observations, METRIC)

    # After adjusting for who they faced, the two defenses are equivalent.
    assert abs(defense[1] - defense[2]) < 1e-6
    # And the schedule difference is attributed to the OFFENCES instead.
    assert offense[s1] > offense[w1]


def test_disconnected_schedules_are_not_separable():
    """A documented LIMITATION, not a defect.

    If two groups of teams never play each other, no additive model can tell
    "these defenses are bad" from "those offences are good" — the data simply
    does not contain that information. The fit splits the difference and reports
    a spurious gap.

    This matters in practice: early in the season the schedule graph is barely
    connected (at as_of_week=2 each defense has ~1.5 games), which is exactly
    when adjusted ratings deserve least trust. It is why shrinkage exists and
    why CLAUDE.md §6 asks for wider uncertainty early.
    """
    observations = []
    for o in (10, 11):                      # island A
        observations += [obs(1, o, 150), obs(3, o, 150)]
    for o in (20, 21):                      # island B, never meets island A
        observations += [obs(2, o, 50), obs(4, o, 50)]

    _, defense, _ = _fit_additive(observations, METRIC)

    # The model reports a large gap even though both defenses are average.
    assert abs(defense[1] - defense[2]) > 50


def test_a_genuinely_bad_defense_still_looks_bad():
    """The adjustment must not launder real weakness into the mean."""
    observations = []
    for o in (10, 11, 12):
        observations.append(obs(1, o, 200))   # genuinely porous
        observations.append(obs(2, o, 100))
        observations.append(obs(3, o, 100))
    _, defense, _ = _fit_additive(observations, METRIC)
    assert defense[1] > defense[2]
    assert defense[1] > 50


def test_missing_metric_rows_are_skipped_not_zeroed():
    """A None ppa must not be read as 0.0, which would drag the mean down."""
    observations = [
        Observation(1, 10, 1, {METRIC: 100.0}),
        Observation(2, 11, 1, {}),  # metric absent
        Observation(3, 12, 1, {METRIC: 200.0}),
    ]
    mean, defense, _ = _fit_additive(observations, METRIC)
    assert mean == 150.0
    assert 2 not in defense


def test_empty_input_is_safe():
    mean, defense, offense = _fit_additive([], METRIC)
    assert mean == 0.0 and defense == {} and offense == {}


def test_shrinkage_moves_toward_the_mean_with_fewer_games():
    """Shrinkage weight is games / (games + k): monotonic and bounded 0..1."""
    def weight(n):
        return n / (n + SHRINKAGE_GAMES)

    assert weight(1) < weight(4) < weight(12) < 1.0
    assert weight(4) == 0.5  # by construction, k games = half weight
    assert 0.0 < weight(1) < 0.5


def test_configuration_constants():
    assert set(SKILL_POSITIONS) == {"QB", "RB", "WR", "TE"}
    # Part of the ratings unique key, so a change must be deliberate: it lets a
    # revised method be computed alongside the old one instead of overwriting.
    assert ADJUSTMENT_VERSION == "v1_iterative_additive"


def test_every_position_ranks_on_a_metric_it_actually_produces():
    """The rank must order by a stat the position genuinely generates.

    QB is why this test exists. Ranking a QB defense by RECEIVING yards allowed
    to quarterbacks passes every structural check — the ranks still come out
    dense, 1..N, no ties — while ordering on trick-play noise averaging half a
    yard a game. The defect is invisible to any guard that only asks whether the
    rank is a valid permutation, so the property has to be stated as "ranks on
    the right column", not "ranks".
    """
    assert set(RANK_METRICS) == set(SKILL_POSITIONS)

    # Quarterbacks throw; they do not catch. Rushing is the ONLY real QB split.
    assert RANK_METRICS["QB"] == "adj_rush_yards_allowed_pg"
    assert RANK_METRICS["RB"] == "adj_rush_yards_allowed_pg"
    assert RANK_METRICS["WR"] == "adj_rec_yards_allowed_pg"
    assert RANK_METRICS["TE"] == "adj_rec_yards_allowed_pg"

    # Each names a real adjusted column on defense_position_ratings.
    for metric in RANK_METRICS.values():
        assert metric.startswith("adj_") and metric.endswith("_pg")
