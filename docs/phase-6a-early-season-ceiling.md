# Phase 6a — the early-season ceiling

Measurement only. No model code was written for this; `blend` and `prior_weight`
were imported from the shipped modules so that the predictor under test is the
centre the board would actually publish, not a re-derivation of it.

The question this phase exists to answer: **the board is empty on opening
weekend and nearly empty the week after, the client has said that is not
acceptable, and weeks 1–2 have never been graded.** Before lowering
`MIN_BACKTEST_WEEK` or touching `MIN_GAMES_TO_PROJECT`, we need to know whether
there is enough signal in a prior season to stand behind a probability.

Method: for every player who appeared in a target week of 2024 or 2025, score
two predictors of that week's output.

* **priors-only** — prior season + position baseline. All a week-1 board can have.
* **in-season** — current-to-date + prior + baseline. What weeks 3+ ship today.

Weeks 3/5/8 are scored with both, so the priors-only column shows how much of
the model's normal skill survives when the current season is taken away. Rank
correlation (Spearman ρ) for continuous markets, AUC for anytime TD, because
ordering the board is what those numbers have to support.

---

## 1. Prior-season usage carries most of the week-3 signal

Pooled 2024 + 2025, players with a prior season. The `wk1 priors-only` column is
what an opening-weekend board would run on; `wk3 in-season` is the first board
we publish today.

| Market | wk1 priors-only | wk3 in-season (shipped) | retained |
|---|---|---|---|
| QB pass yards | 0.463 | 0.620 | 75% |
| QB pass attempts | 0.458 | 0.595 | 77% |
| QB pass completions | 0.477 | 0.623 | 77% |
| QB pass TDs | 0.325 | 0.386 | 84% |
| QB rush yards | 0.478 | 0.424 | **113%** |
| RB rush yards | 0.359 | 0.427 | 84% |
| RB rush attempts | 0.417 | 0.550 | 76% |
| RB receptions | 0.249 | 0.165 | **151%** |
| RB rec yards | 0.163 | 0.101 | **161%** |
| WR receptions | 0.331 | 0.462 | 72% |
| WR rec yards | 0.300 | 0.391 | 77% |
| TE receptions | 0.344 | 0.407 | 85% |
| TE rec yards | 0.244 | 0.369 | 66% |
| QB anytime TD *(AUC)* | 0.682 | 0.655 | **104%** |
| RB anytime TD *(AUC)* | 0.630 | 0.621 | **101%** |
| WR anytime TD *(AUC)* | 0.577 | 0.602 | 96% |
| TE anytime TD *(AUC)* | 0.608 | 0.484 | **126%** |

**A week-1 priors-only board retains roughly 72–85% of the rank signal the
week-3 board carries, and in five markets it beats it outright.** That last part
is the finding worth pausing on: at week 3 the model leans on a one-or-two-game
current-season sample and discounts the prior season by a third, and for
anytime TD and the low-volume receiving markets that is a *worse* information
set than a clean full prior season. The week-3 board is not the well-informed
thing that weeks 1–2 fall short of.

Note also how little of the season it takes for the in-season predictor to stop
improving: QB pass yards peaks at week 5 (0.719) and falls back to 0.610 by
week 8. The advantage of within-season data is real but bounded.

## 2. Transfers are where the signal actually goes

Week 1, split by whether the player is on the same team as last season. 23.6%
of the 2024 candidate pool changed team, 30.2% of the 2025 pool.

| Market | same team | changed team | lost |
|---|---|---|---|
| QB pass attempts | 0.525 | 0.302 | −42% |
| QB pass completions | 0.534 | 0.340 | −36% |
| QB pass yards | 0.489 | 0.387 | −21% |
| QB rush yards | 0.481 | 0.482 | — |
| RB rush yards | 0.421 | 0.170 | **−60%** |
| RB rush attempts | 0.481 | 0.208 | **−57%** |
| RB receptions | 0.303 | 0.101 | **−67%** |
| RB rec yards | 0.190 | 0.098 | −48% |
| WR receptions | 0.402 | 0.112 | **−72%** |
| WR rec yards | 0.364 | 0.124 | **−66%** |
| TE receptions | 0.365 | 0.327 | −10% |
| TE rec yards | 0.240 | 0.284 | +18% |
| QB anytime TD *(AUC)* | 0.670 | 0.724 | +8% |
| RB anytime TD *(AUC)* | 0.626 | 0.639 | +2% |
| WR anytime TD *(AUC)* | 0.599 | 0.522 | −13% |
| TE anytime TD *(AUC)* | 0.609 | 0.620 | +2% |

Two clean splits fall out of this, and they are not the same split:

* **Volume and yardage markets collapse for transfers.** WR receptions at
  ρ = 0.112 and RB rush attempts at ρ = 0.208 are close to worthless. This is
  exactly what `CHANGED_TEAM_PRIOR_MULTIPLIER` was written for — usage is a
  property of the depth chart a player left behind — and the measurement says
  the haircut is directionally right but too small for WR and RB receiving.
* **Anytime TD does not care.** AUC holds at 0.52–0.72 for transfers, and for
  QBs is *higher* than for stayers. Scoring propensity travels with the player;
  target share does not.

`TE changed team` at week 2 collapses to ρ ≈ 0.01 on n ≈ 67 pooled. That is a
thin cell, not a finding — do not build a rule on it.

## 3. Coverage: about a quarter of week-1 production is unreachable

Of the players who actually produced in week 1, those with any prior season to
project from:

| Season | played wk1 | has prior | coverage |
|---|---|---|---|
| 2024 | 1,630 | 1,211 | 74.3% |
| 2025 | 1,692 | 1,199 | 70.9% |

The remaining 26–29% are true freshmen, JUCO and FCS arrivals. **No amount of
modelling reaches them**, and the board should not pretend otherwise. This is a
hard ceiling on opening-weekend completeness, separate from accuracy.

## 4. The week-1 board would be no worse at picking who plays

The objection to a priors-only board is that nothing filters out players who
will not dress — `MIN_GAMES_TO_PROJECT` does that job from week 3. Measured, it
is a much smaller problem than expected.

| Universe rule | candidates | actually played | precision |
|---|---|---|---|
| wk1: prior usage + on a roster (2024) | 1,937 | 1,166 | 60.2% |
| wk1: prior usage + on a roster (2025) | 1,816 | 1,140 | 62.8% |
| wk1: **+ ≥4 prior-season games** (2024) | 1,280 | 898 | **70.2%** |
| wk1: **+ ≥4 prior-season games** (2025) | 1,161 | 852 | **73.4%** |
| wk3 shipped rule: ≥2 games played (2024) | 1,108 | 791 | 71.4% |
| wk3 shipped rule: ≥2 games played (2025) | 1,182 | 844 | 71.4% |

**Requiring four prior-season games makes an opening-weekend board exactly as
good at guessing who dresses as the week-3 board we already ship** — 70–73%
against 71.4% — on a candidate pool of comparable size. The "did not play"
objection does not survive contact with the data.

## 5. The defense panels have the same cold start, and worse

Confirmed: `defense_position_ratings` has **no `as_of_week = 1` rows at all**, in
either season. The ladder starts at week 2 and is badly incomplete there:

| Season | as_of_week 2 | as_of_week 3 | as_of_week 4 |
|---|---|---|---|
| 2024 | 75 of 134 teams | 130 | 134 |
| 2025 | 93 of 136 teams | 135 | 136 |

So the opponent adjustment, the `rank_vs_position` filter and the "who to
target" panel are all blank on opening weekend and half-blank the week after.
`matchup_multiplier` returns 1.0 on a null allowance, so nothing breaks — the
week-1 board simply has no matchup signal, silently.

**Seeding week 1 from the prior season is not justified.** Correlation between a
defense's end-of-2024 rating and its end-of-2025 rating:

| Position | rush yds allowed | rec yds allowed |
|---|---|---|
| RB | **0.550** | 0.110 |
| WR | 0.215 | 0.385 |
| QB | 0.234 | 0.000 |
| TE | 0.173 | 0.125 |

Only RB rushing carries meaningfully year to year. Everything else is between
noise and nothing. Carrying last season's ratings into week 1 would import
confident numbers that are not about this season's defense.

`defense_position_game_splits` *does* hold week-1 rows (135–141 teams), so the
defense **game log** is populated from the first week onward — of the season
being played. Entering week 1 there is nothing to show but the prior season, and
if we show it, it must be labelled as the prior season.

## 6. Is the 2023 prior deep enough to grade 2024 weeks 1–2?

Yes, with one exception that matters. 2023 holds 17,947 player-game rows across
910 games — the same shape as 2024 (18,060) and 2025 (18,750) — and non-NULL
coverage matches season for season:

| Column | 2023 | 2024 | 2025 |
|---|---|---|---|
| pass_* | 14.5% | 14.2% | 14.6% |
| rush_* | 49.4% | 49.9% | 49.9% |
| receptions / rec_yards / rec_tds | 69.4% | 69.5% | 69.8% |
| offensive_tds | 100% | 100% | 100% |
| **targets** | **0.0%** | 70.1% | 72.3% |
| snaps | 0.0% | 0.0% | 0.0% |

**`targets` is entirely absent from 2023** (`snaps` is empty in every season and
is not a 2023 problem). That is not cosmetic: receptions are modelled as a
beta-binomial through targets × catch rate, so for 2024 weeks 1–2 both the
volume and the rate terms fall back to the position baseline and every receiver
gets the same number. The measurement in §1 blends `receptions` directly and so
is *optimistic* about what the shipped model would produce for 2024 weeks 1–2.

Consequence for 6b: **grade 2025 weeks 1–2 as the primary evidence and treat
2024 as secondary.** 2025's prior is 2024, which has targets; that is the
configuration 2026 will actually run in.

Separately, `play_player_stats` holds no 2023 data at all — but
`player_goal_line_usage` reads the current season only, so goal-line features
are empty in week 1 of *any* season. The anytime-TD model's opportunity ×
finish-rate decomposition has no opportunity term on opening weekend regardless
of backfill, and falls back to the blended rate.

## 7. The defect that dominates the phase: week-1 distributions are too narrow

`_sd(mean, observed_sd)` reads `{stat}_sd` — the current season only. At week 1
that column does not exist, so the projected SD falls to its floor,
`MIN_RELATIVE_SD × mean` = 0.25 × mean. `_dispersion` behaves the same way and
returns its 2.0 default.

Measured against how much a player's output actually varies week to week
(2024, players with ≥6 games):

| Market | real sd/mean | week-1 floor | too narrow by |
|---|---|---|---|
| QB rush yards | 1.14 | 0.25 | **4.6×** |
| QB pass TDs | 0.89 | 0.25 | **3.5×** |
| RB rec yards | 0.85 | 0.25 | 3.4× |
| RB rush yards | 0.76 | 0.25 | 3.0× |
| TE rec yards | 0.75 | 0.25 | 3.0× |
| WR rec yards | 0.70 | 0.25 | 2.8× |
| RB rush attempts | 0.52 | 0.25 | 2.1× |
| WR receptions | 0.52 | 0.25 | 2.1× |
| TE receptions | 0.50 | 0.25 | 2.0× |
| RB receptions | 0.47 | 0.25 | 1.9× |
| QB pass yards | 0.46 | 0.25 | 1.8× |
| QB pass completions | 0.41 | 0.25 | 1.6× |
| QB pass attempts | 0.40 | 0.25 | 1.6× |

### The empirical variance layer already absorbs much of this

That table is the gap **before** calibration, and quoting it as the gap on the
board would overstate the defect. `Corrections.variance` buckets on
`history_bucket(games_played)`, and a week-1 row (0 games) lands in `thin`
alongside the 2–3 game rows the current walk fits on. The scales it learned,
read off the last run's `calibration` metadata:

| Market | fitted `@thin` width scale |
|---|---|
| rec_yards | ×2.170 |
| rush_yards | ×2.149 |
| rush_attempts | ×1.377 |
| pass_attempts | ×1.549 *(market-level; no thin cell reached n)* |
| pass_yards | ×1.514 *(market-level)* |
| receptions | ×0.927 — the layer **narrows** this one |

Netting the two, a published week-1 distribution would be roughly **1.2× to
2.1×** too narrow rather than 1.6–4.6×. Still wrong, still all in the same
direction, but the board is not as naked as the raw floor suggests.

### What the layer structurally cannot fix, and why the fix still comes first

1. **It cannot restore per-player shape.** A multiplicative scale on a constant
   floor is still a constant: every week-1 receiver would carry the identical
   relative width, so a metronomic possession receiver and a boom-bust deep
   threat get the same confidence off the same mean. Reading
   `prior_{stat}_sd` is the only thing that differentiates them.
2. **It never reaches anytime TD.** Every `anytime_td` width correction is
   recorded `applied: False — family has no free width parameter`, at every
   bucket and position. The binary market is uncorrected by construction, and
   §1 says it is the market that holds up *best* at week 1 — so it is the one
   most likely to be published, with no width safety net under it.
3. **The bucket boundary is about to stop meaning anything.** These scales were
   fitted where `{stat}_sd` exists (2–3 games). Letting weeks 1–2 into the walk
   puts 0-game rows, whose SD is a pure floor, in the same `thin` cell — mixing
   two different regimes in one average, which is precisely what
   `history_bucket`'s docstring says bucketing on games rather than weeks exists
   to avoid.

The fix is cheap: `features.prior_column_names()` already materializes
`prior_{stat}_sd` into the frame and `models.py` never reads it. Nothing needs
to be ingested.

**It also cannot be done after grading.** Widening a distribution raises
`observed_sd`, and QB rush yards sets its gamma location at
`min(0.0, mean - 3.0 × observed_sd)` — so a wider SD drives `loc` further
negative, which is the mechanism behind the 140 negative medians Phase 5 handed
forward. The width fix and the negative-median fix are the same fix, and doing
either alone makes the other worse.

---

## Recommendation

**Ship an opening-weekend board. Scope it by evidence, not by calendar.** The
gate remains 6b's calibration; nothing below should be published before it
grades.

1. **Fix the width first, in 6b, before grading anything** — together with the
   gamma location, because they are one fix (§7). Read `prior_{stat}_sd` in
   `_sd` and `_dispersion`. The empirical variance layer already covers most of
   the *average* level, so this is not the emergency the raw floor implies; what
   it buys is per-player width differentiation, an anytime-TD market that has
   any width discipline at all, and a `thin` bucket that is not silently
   averaging 0-game and 3-game rows once weeks 1–2 join the walk.
2. **Universe rule for weeks 1–2: prior-season skill usage, ≥4 prior games, on a
   current roster.** Replaces `MIN_GAMES_TO_PROJECT` for those weeks rather than
   relaxing it. Justified by §4 — it matches the week-3 board's did-they-play
   precision — and it sizes the board at ~1,150–1,280 players, comparable to the
   ~1,100–1,180 the week-3 rule admits.
3. **Do not seed the opponent adjustment from the prior season.** Leave it
   neutral, which `matchup_multiplier` already does gracefully. Say so on the
   board rather than letting the absence be silent. The one defensible exception
   is RB rushing (r = 0.55), and it is not worth a special case on its own.
4. **Keep transfers on the board, but not on every market.** Anytime TD holds up
   for transfers and should carry them. For WR/RB receiving volume the prior is
   near-worthless (ρ ≈ 0.10–0.12) and `CHANGED_TEAM_PRIOR_MULTIPLIER = 0.5` is
   too generous in weeks 1–2 — 6b should fit the discount rather than keep
   asserting it.
5. **Grade 2025 weeks 1–2 as the primary evidence.** 2024's prior season has no
   `targets`, which degrades every receptions market to baseline and is not the
   configuration 2026 will run in.
6. **Expect ~70% coverage and state it.** A quarter of opening-weekend
   production comes from players with no prior season. The board should be
   visibly incomplete rather than quietly so.

### Still blocking, and not fixable here

`player_team_seasons` holds **0 rows for 2026**, and there are **0 Elo snapshots
for 2026**. The 2026 schedule is loaded (888 games, 684 team-seasons), so the
gap is rosters specifically. **No roster, no player-team mapping, no 29 Aug
board, however well 6b grades.** This is a hard external dependency on CFBD and
needs chasing in parallel with 6b, not after it.
