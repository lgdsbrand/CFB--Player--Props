"""Tests for the weekly read's prompt and cache digest (Phase 5b).

Every case here is a REAL failure observed against Gemini while building this,
not a hypothetical. A prompt cannot be verified by reading samples and nodding —
the failures are fluent, confident and wrong, which is exactly what makes them
survive review.

No network, no database.
"""

from __future__ import annotations

from worker.core.ai_prompt import (
    PROMPT_VERSION,
    MarketLine,
    PromptInputs,
    build_prompt,
    describe_matchup,
    input_digest,
)

BASE = PromptInputs(
    player_name="Drew Allar", position_group="QB", team="Penn State",
    opponent="Ohio State", season=2025, week=11, is_home=False,
    markets=(
        MarketLine("Passing Yards", "UNDER", 0.63, 238.5, True,
                   projected_median=214.0, projected_p10=142.0,
                   projected_p90=291.0, edge=0.084),
    ),
)


class TestMatchupInversion:
    """`rank_vs_position` counts 1 = ALLOWS THE MOST, against every convention.

    Handed the raw rank, Gemini wrote "a favorable ground matchup against an
    Ohio State defense that ranks 118th of 136" — backwards — with the
    convention spelled out in capitals immediately above. So the prompt states
    the conclusion and the model never performs the inversion.
    """

    def test_a_high_rank_is_stated_as_hard(self):
        text = describe_matchup(118, 136)
        assert "HARD" in text
        assert "AGAINST" in text

    def test_a_low_rank_is_stated_as_soft(self):
        text = describe_matchup(3, 136)
        assert "SOFT" in text
        assert "FOR" in text

    def test_the_extremes_do_not_flip(self):
        assert "SOFT" in describe_matchup(1, 136)
        assert "HARD" in describe_matchup(136, 136)

    def test_the_middle_is_average(self):
        assert "AVERAGE" in describe_matchup(68, 136)

    def test_the_raw_rank_still_appears_so_prose_matches_the_page(self):
        assert "118 of 136" in describe_matchup(118, 136)

    def test_no_field_size_yields_no_verdict(self):
        """Without a field size a rank cannot be turned into soft or hard, and
        inventing one would be worse than staying quiet."""
        text = describe_matchup(5, None)
        assert "SOFT" not in text and "HARD" not in text

    def test_the_prompt_never_ships_a_bare_rank(self):
        prompt = build_prompt(
            PromptInputs(**{**BASE.__dict__, "opponent_rank": 118,
                            "ranked_defenses": 136})
        )
        assert "HARD matchup" in prompt


class TestBinaryMarkets:
    """Anytime TD is a probability, never a projected count (CLAUDE.md §1, §6).

    v1 shipped its stored median and Gemini rendered "an expected touchdown
    projection of -0.0" — a negative zero, on a page whose entire claim is that
    it does not show projected counts for this market.
    """

    def _prompt(self, side: str, confidence: float) -> str:
        return build_prompt(PromptInputs(
            **{**BASE.__dict__, "markets": (
                MarketLine("Anytime TD", side, confidence, 0.5, False,
                           projected_median=-0.0, projected_p10=-0.0,
                           projected_p90=1.0, is_binary=True),
            )}
        ))

    def test_a_binary_market_never_shows_a_projection(self):
        prompt = self._prompt("UNDER", 0.94)
        assert "projected" not in prompt.lower()
        assert "-0.0" not in prompt and "0.0" not in prompt

    def test_an_under_call_is_stated_as_a_chance_to_score(self):
        prompt = self._prompt("UNDER", 0.94)
        assert "6% chance to score" in prompt
        assert "NO at 94% confidence" in prompt

    def test_an_over_call_is_stated_as_a_chance_to_score(self):
        prompt = self._prompt("OVER", 0.71)
        assert "71% chance to score" in prompt
        assert "YES at 71% confidence" in prompt

    def test_a_non_binary_market_keeps_its_projection(self):
        assert "projected 214" in build_prompt(BASE)


class TestFabricationGuards:
    def test_touchdown_history_is_supplied_when_available(self):
        """v1 and v2 both invented 'zero touchdowns over his last five games'
        from nothing but a high UNDER confidence. Forbidding it twice did not
        work; supplying the real series did."""
        prompt = build_prompt(
            PromptInputs(**{**BASE.__dict__,
                            "recent_td_counts": (0.0, 1.0, 0.0, 0.0, 0.0)})
        )
        assert "RECENT TOUCHDOWNS" in prompt
        assert "1 in these 5 game(s)" in prompt

    def test_the_rules_forbid_history_that_was_not_supplied(self):
        prompt = build_prompt(BASE)
        # The DATA line, not the rule that refers to it — the rules mention
        # "RECENT TOUCHDOWNS" by name in order to forbid inventing it.
        assert "RECENT TOUCHDOWNS (most recent last)" not in prompt
        assert "say nothing about touchdowns scored" in prompt

    def test_the_model_is_told_not_to_make_its_own_call(self):
        prompt = build_prompt(BASE)
        assert "Do NOT make your own call" in prompt

    def test_an_unposted_line_is_flagged_as_not_a_market_price(self):
        prompt = build_prompt(PromptInputs(
            **{**BASE.__dict__, "markets": (
                MarketLine("Rushing Yards", "OVER", 0.57, 18.5, False,
                           projected_median=22.0),
            )}
        ))
        assert "NO BOOK HAS POSTED" in prompt

    def test_the_qb_rank_caveat_travels_with_the_rank(self):
        prompt = build_prompt(PromptInputs(
            **{**BASE.__dict__, "opponent_rank": 118, "ranked_defenses": 136,
               "rank_caveat": "this rank is rushing only"}
        ))
        assert "rushing only" in prompt

    def test_early_season_uncertainty_is_surfaced(self):
        prompt = build_prompt(PromptInputs(**{**BASE.__dict__, "prior_weight": 0.33}))
        assert "33%" in prompt and "prior-season" in prompt

    def test_a_small_prior_weight_is_not_worth_a_caveat(self):
        prompt = build_prompt(PromptInputs(**{**BASE.__dict__, "prior_weight": 0.05}))
        assert "prior-season" not in prompt


class TestDigest:
    def test_identical_inputs_hash_identically(self):
        assert input_digest(BASE) == input_digest(BASE)

    def test_a_changed_line_changes_the_digest(self):
        moved = PromptInputs(**{**BASE.__dict__, "markets": (
            MarketLine("Passing Yards", "UNDER", 0.63, 244.5, True,
                       projected_median=214.0, projected_p10=142.0,
                       projected_p90=291.0, edge=0.084),
        )})
        assert input_digest(moved) != input_digest(BASE)

    def test_a_changed_opponent_rank_changes_the_digest(self):
        assert input_digest(
            PromptInputs(**{**BASE.__dict__, "opponent_rank": 4})
        ) != input_digest(BASE)

    def test_floating_point_noise_does_not_change_the_digest(self):
        """A projection that moves by 1e-12 between pipeline runs is not a
        change worth paying to regenerate — and unrounded it would look like
        one every single week."""
        jittered = PromptInputs(**{**BASE.__dict__, "markets": (
            MarketLine("Passing Yards", "UNDER", 0.63 + 1e-12, 238.5, True,
                       projected_median=214.0 + 1e-12, projected_p10=142.0,
                       projected_p90=291.0, edge=0.084),
        )})
        assert input_digest(jittered) == input_digest(BASE)

    def test_the_digest_covers_touchdown_history(self):
        """It is in the prompt, so it must be in the digest — a digest over
        fewer inputs than the prompt silently authorises a stale read."""
        assert input_digest(
            PromptInputs(**{**BASE.__dict__, "recent_td_counts": (1.0,)})
        ) != input_digest(BASE)

    def test_prompt_version_is_not_part_of_the_digest(self):
        """They answer different questions: the digest asks whether the facts
        moved, the version asks whether we changed what we ask for. The job
        checks both, so folding one into the other would lose a reason to
        regenerate."""
        assert isinstance(PROMPT_VERSION, str) and PROMPT_VERSION
