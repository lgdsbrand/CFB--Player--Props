"""College live player stats (migration 0092) — offline.

Every play text below is CFBD's own, from /live/plays for 2026 week 6
(Hawaii @ Arizona State, South Alabama @ Arkansas State, Iowa @ Washington,
Utah State @ Washington State, Iowa State @ BYU). What is pinned: the three
text formats the feed mixes, the play-type labels it gets wrong, yards from
the text rather than `yardsGained`, wiped-out plays, the conversion after a
touchdown, and roster resolution by jersey and surname that refuses to guess.
"""

from __future__ import annotations

from datetime import UTC, datetime

from worker.adapters.cfbd.live_player_stats import (
    RosterResolver,
    StatLine,
    parse_live_game,
    stat_rows,
)

ASU, HAW = 9, 62


def game(*plays: tuple[int, str, str, int]) -> dict:
    """plays: (team, playType, playText, yardsGained)."""
    return {
        "drives": [
            {
                "plays": [
                    {"teamId": team, "playType": kind, "playText": text, "yardsGained": yards}
                    for team, kind, text, yards in plays
                ]
            }
        ]
    }


def test_a_completion_credits_the_passer_and_the_receiver():
    stats = parse_live_game(
        game(
            (
                HAW,
                "Pass Reception",
                "(14:55) Shotgun #12 M.Alejado pass complete deep middle to #11 D.Alves "
                "caught at ASU35, "
                "for 47 yards to the ASU28 (#1 A.Stamps), 1ST DOWN",
                47,
            )
        )
    )
    assert stats[(HAW, 12, "M.Alejado")] == StatLine(
        pass_attempts=1, pass_completions=1, pass_yards=47
    )
    assert stats[(HAW, 11, "D.Alves")] == StatLine(receptions=1, rec_yards=47)
    assert (ASU, 1, "A.Stamps") not in stats, "a tackler is not a player in the play"


def test_a_touchdown_counts_from_the_text_even_when_the_type_says_otherwise():
    stats = parse_live_game(
        game(
            (
                ASU,
                "Pass Reception",
                "(02:00) No Huddle-Shotgun #8 C.Boley pass complete short right to #3 R.Harris "
                "caught at HAW12, for 9 yards to the HAW00 TOUCHDOWN, clock 01:55, 1ST DOWN "
                "#8 C.Boley pass attempt failed",
                9,
            )
        )
    )
    assert stats[(ASU, 3, "R.Harris")].offensive_tds == 1
    assert stats[(ASU, 8, "C.Boley")].pass_tds == 1
    # The failed two-point try after ", clock" is not a second pass.
    assert stats[(ASU, 8, "C.Boley")].pass_attempts == 1


def test_rushes_losses_and_sacks_take_their_yards_from_the_text():
    stats = parse_live_game(
        game(
            (
                ASU,
                "Rush",
                "(13:50) Shotgun #21 L.Phillips Jr. rush left for 11 yards gain to the WASH31 "
                "(#6 D.Robinson), out of bounds, 1ST DOWN",
                11,
            ),
            (
                ASU,
                "Rush",
                "(13:20) #21 L.Phillips Jr. rush right for 2 yards loss to the WASH33 (#48 J.Lane)",
                -2,
            ),
            # yardsGained says -3; the text says 6, and the box score agrees with the text.
            (
                ASU,
                "Sack",
                "(06:58) Shotgun #1 J.Raynor sacked for loss of 6 yards to the BYU49 (#90 H.Clegg)",
                -3,
            ),
            (
                ASU,
                "Rush",
                "(12:31) No Huddle-Shotgun #1 K.Brown rush right for 0 yards to the ASU32",
                0,
            ),
        )
    )
    assert stats[(ASU, 21, "L.Phillips Jr.")] == StatLine(rush_attempts=2, rush_yards=9)
    assert stats[(ASU, 1, "J.Raynor")] == StatLine(rush_attempts=1, rush_yards=-6)
    assert stats[(ASU, 1, "K.Brown")] == StatLine(rush_attempts=1, rush_yards=0)


def test_a_play_wiped_out_by_a_penalty_counts_for_nothing_but_one_that_stood_counts():
    stats = parse_live_game(
        game(
            (
                ASU,
                "Penalty",
                "(04:50) No Huddle-Shotgun #12 C.Pinnick rush right for 16 yards gain to the "
                "USU31, out of bounds at USU31 PENALTY WSU Holding (#77 N.Bakken) 10 yards from "
                "USU47 to WSU43. NO PLAY",
                10,
            ),
            (
                ASU,
                "Rush",
                "(13:15) No Huddle-Shotgun #12 C.Pinnick rush middle for 10 yards gain to the "
                "WSU49, End Of Play, 1ST DOWN, PENALTY USU UNR: Unnecessary Roughness "
                "(#2 K.Stokes) 15 yards "
                "from WSU49 to USU36, 1ST DOWN",
                10,
            ),
            (
                ASU,
                "Fumble Recovery (Opponent)",
                "(08:18) No Huddle-Shotgun #12 C.Pinnick pass complete short "
                "right to #28 J.Pedersen caught at WSU17, for 8 yards to the WSU19 fumbled by #28 "
                "J.Pedersen at WSU19 forced by #11 A.Cunningham",
                8,
            ),
        )
    )
    assert stats[(ASU, 12, "C.Pinnick")] == StatLine(
        rush_attempts=1,
        rush_yards=10,
        pass_attempts=1,
        pass_completions=1,
        pass_yards=8,
    )
    assert stats[(ASU, 28, "J.Pedersen")] == StatLine(receptions=1, rec_yards=8)


def test_the_two_summary_formats_are_read_with_full_and_short_names():
    stats = parse_live_game(
        game(
            (
                ASU,
                "Passing Touchdown",
                "Bryson Rodgers 42 Yd pass from Michael Van Buren Jr. (Nico Gramatica Kick)",
                42,
            ),
            (ASU, "Passing Touchdown", "Mason James 3 Yd pass from Elijah Brown ", 3),
            (ASU, "Rushing Touchdown", "A'Marion Peterson 3 Yd Run (Michael Petro Kick)", 3),
            (
                ASU,
                "Passing Touchdown",
                "T. Owens pass to H. Summers for 20 yds, for a TD (C. Van Andel KICK)",
                20,
            ),
            (ASU, "Rushing Touchdown", "K. Phillips run for 42 yds, for a TD (D. Little KICK)", 42),
        )
    )
    assert stats[(ASU, None, "Bryson Rodgers")] == StatLine(
        receptions=1, rec_yards=42, offensive_tds=1
    )
    assert stats[(ASU, None, "Michael Van Buren Jr.")].pass_yards == 42
    assert stats[(ASU, None, "Elijah Brown")].pass_tds == 1
    assert stats[(ASU, None, "A'Marion Peterson")] == StatLine(
        rush_attempts=1, rush_yards=3, offensive_tds=1
    )
    assert stats[(ASU, None, "H. Summers")] == StatLine(receptions=1, rec_yards=20, offensive_tds=1)
    assert stats[(ASU, None, "K. Phillips")] == StatLine(
        rush_attempts=1, rush_yards=42, offensive_tds=1
    )


def roster():
    return [
        {"id": 1, "name": "Reed Harris", "jersey": 3, "games": 5},
        {"id": 2, "name": "Rashad Harris", "jersey": 21, "games": 5},
        {
            "id": 3,
            "name": "Kyle Monroe",
            "jersey": 3,
            "games": 5,
        },  # a defender with the same number
        {"id": 4, "name": "L.J. Phillips Jr.", "jersey": 21, "games": 5},
        {"id": 5, "name": "Reece Vander Zee", "jersey": 2, "games": 5},
    ]


def test_names_resolve_by_jersey_and_surname_and_never_by_a_guess():
    r = RosterResolver(roster())
    assert r.resolve(3, "R.Harris") == 1, "jersey 3 is shared; the surname decides"
    assert r.resolve(21, "L.Phillips Jr.") == 4
    assert r.resolve(2, "R.Vander Zee") == 5
    assert r.resolve(None, "Reed Harris") == 1
    # Two R. Harrises and no jersey: refused rather than guessed.
    assert r.resolve(None, "R. Harris") is None
    assert r.resolve(88, "Z.Nobody") is None


def test_rows_sum_one_player_named_two_ways_and_report_the_unresolved():
    parsed = {
        (ASU, 3, "R.Harris"): StatLine(receptions=4, rec_yards=60),
        (ASU, None, "Reed Harris"): StatLine(receptions=1, rec_yards=18, offensive_tds=1),
        (ASU, 88, "Z.Nobody"): StatLine(receptions=1, rec_yards=5),
        (HAW, 12, "M.Alejado"): StatLine(pass_attempts=1),
    }
    now = datetime(2026, 10, 17, 21, tzinfo=UTC)
    rows, unresolved = stat_rows(parsed, {ASU: (500, RosterResolver(roster()))}, 6921, now)
    assert rows == [
        {
            "game_id": 6921,
            "player_id": 1,
            "team_id": 500,
            **vars(StatLine(receptions=5, rec_yards=78, offensive_tds=1)),
            "updated_at": now,
        }
    ]
    assert sorted(unresolved) == ["M.Alejado", "Z.Nobody"]
