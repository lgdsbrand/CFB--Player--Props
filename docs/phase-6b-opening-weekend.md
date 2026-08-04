# Phase 6b — grading the opening weekends

**The review gate.** Weeks 1 and 2 had never been scored by any walk
(`MIN_BACKTEST_WEEK` was 3), so "can the board open with the season" had no
numeric answer.

Two runs stand behind this document, both 2024 and 2025, point-in-time:

| Run | | n |
|---|---|---|
| `d606279b` | the gate: weeks 1-2 graded for the first time | 360,725 |
| `0930961f` | after the fix that grading exposed | 360,058 |

Companion documents: [phase-6a-early-season-ceiling.md](phase-6a-early-season-ceiling.md)
measured the ceiling before any of this was graded;
[calibration-report.html](calibration-report.html) is the rendered report.

---

## The answer

**Ship both opening weekends.** After the fix, weeks 1-2 are the
**best-calibrated stratum of the season** and carry its highest skill.

| Stratum | n | Brier skill | ECE |
|---|---|---|---|
| `wk1-2 opening` | 38,971 | **+0.2104** | **0.0184** |
| `wk3-6 early` | 95,021 | +0.1874 | 0.0191 |
| `wk7+ late` | 226,066 | +0.1971 | 0.0215 |
| overall | 360,058 | +0.1960 | 0.0162 |

That is not a claim the opening board knows as much as a settled one. It knows
less, and it now *says* so honestly — which is the only property CLAUDE.md §6
asks for.

---

## 1. What the gate found

Before the fix:

| Stratum | n | Brier skill | ECE |
|---|---|---|---|
| `wk1-2 opening` | 39,638 | +0.1778 | **0.0558** |
| `wk3-6 early` | 95,021 | +0.1885 | 0.0182 |
| `wk7+ late` | 226,066 | +0.1960 | 0.0243 |

Skill was fine. Calibration was three times worse than any other stratum, and
splitting the two opening weeks located it precisely:

| 2025 | n | Brier skill | ECE |
|---|---|---|---|
| week 1 | 12,506 | +0.1611 | **0.0780** |
| week 2 | 10,933 | +0.2130 | 0.0240 |
| weeks 3+ | 163,551 | +0.1945 | 0.0163 |

**Week 2 already graded better than the season it opens.** One completed game is
enough. Week 1 was the whole defect, and it showed up where it costs most —
stated 0.87 against 0.837 actual at the top of the confidence range.

The calibration layer named the mechanism without identifying it: the `@priors`
**mean** multiplier sat pinned at `MAX_MEAN_MULTIPLIER` (x1.200) for rec_yards,
receptions and rush_yards. A binding clamp is not a correction, it is a report
that the projection is biased low by more than the layer is permitted to repair.

## 2. The cause was not what this document first said

The first version of this write-up blamed **survivorship in the anchor**: a
week-1 projection rests on the player's full prior-season average, and players
who dress in week 1 disproportionately *ended* last season with a role, so a
full-season average should understate them. The prescribed fix was to blend the
anchor toward `prior_{stat}_recent_pg`, the last five games.

**Measured, that hypothesis is wrong twice over.** Over the graded population
the two anchors barely differ:

| stat | prior full season | prior last 5 | ratio |
|---|---|---|---|
| pass_yards | 142.85 | 141.67 | 0.992 |
| rec_yards | 26.67 | 26.78 | 1.004 |
| receptions | 2.29 | 2.34 | 1.020 |
| rush_yards | 27.55 | 28.11 | 1.020 |
| rush_attempts | 5.52 | 5.78 | 1.046 |

And running the real projector at anchor weights from 0 to 1 moves the bias
almost not at all — `rec_yards` gets *worse* (1.271 → 1.294), `pass_attempts`
improves only 1.319 → 1.289. Had the fix been implemented as recommended it
would have shipped, changed nothing measurable, and left the clamp binding.

## 3. The cause was the shrinkage target

The projection is an empirical-Bayes blend of the player's own record and a
position baseline. Entering week 1 the player's own record is a prior season
discounted by `prior_weight` (0.5, halved again for transfers), so the baseline
term carries roughly 40% of the projection — far more than in any settled week.

And that baseline was measured on the wrong population. In weeks 3+ it is a
median over rows **with current-season games**, which is an implicit role
filter: only players who have appeared vote. Entering week 1 nobody has appeared,
the prior-season fallback added in 6b.2 takes over, and its pool was *every*
player on a roster with any prior production at all — third-string quarterbacks
included.

| 2025 week 1 | baseline used | median of the players it was applied to | ratio |
|---|---|---|---|
| QB pass_yards | 91.44 | 185.67 | **2.03** |
| TE rec_yards | 15.00 | 23.50 | 1.57 |
| WR rec_yards | 24.77 | 33.92 | 1.37 |
| RB rush_yards | 25.71 | 32.50 | 1.26 |

A baseline is what a projection with no evidence of its own shrinks toward. This
one was shrinking every opening-weekend projection toward the bench.

## 4. The fix, and how the threshold was chosen

`MIN_PRIOR_GAMES_FOR_BASELINE = 8`: a row votes on the prior-season baseline
only if it played at least eight games of that season. The current-season pool
already carries the equivalent filter; this gives the prior-season pool the same
one.

The threshold was swept rather than picked. Ratio of actual to projected, worst
of the eight markets:

| min prior games | 0 | 4 | 5 | 6 | 7 | **8** | 9 | 10 | 12 |
|---|---|---|---|---|---|---|---|---|---|
| 2025 wk1 | 1.430 | 1.319 | 1.295 | 1.243 | 1.205 | **1.175** | 1.137 | 1.116 | 1.035 |
| 2024 wk1 | 1.585 | 1.331 | 1.261 | 1.243 | 1.191 | **1.116** | 1.095 | 1.064 | 0.971 |

The curve is monotone, not U-shaped, so there is no optimum to find — only the
question of how strong a filter the evidence justifies. **Eight is the smallest
threshold at which every market in both seasons lands inside the ±20% the mean
correction is allowed to apply**, which is the entire point of fixing this at
source; whatever bias remains is then the layer's job, and it can now do it.
Going further looks better on one season and worse on the other (2024's worst
market reaches 0.971 at twelve — over-projecting), which is the signature of
fitting the residual rather than removing a cause. It also thins the pool: at
eight the smallest position still holds 88 players, at twelve only 19.

Note this is deliberately **not** the same number as
`MIN_PRIOR_GAMES_TO_PROJECT = 4`. Those constants answer different questions —
who is worth projecting, and who describes the position. Four is generous for
the first and measurably too generous for the second (1.319 and 1.331 above,
still outside the clamp).

**An empty pool is worse than a broad one**, so where the filter cannot reach
`MIN_BASELINE_SAMPLES` the unfiltered prior pool still answers. A missing
baseline reads as 0.0 downstream: the blend would shrink toward nothing and the
usage floor would stop filtering. On real frames the fallback never fires; it
exists so a thinner backfill degrades instead of disappearing.

The **CV baseline was deliberately left alone.** A coefficient of variation is
scale-free — a backup's week-to-week swing relative to his own average is a
reasonable estimate of a starter's relative swing, while his yards per game is
not a reasonable estimate of a starter's yards per game.

## 5. What it did

| 2025 | skill before | after | ECE before | after |
|---|---|---|---|---|
| week 1 | +0.1611 | **+0.2050** | 0.0780 | **0.0228** |
| week 2 | +0.2130 | +0.2134 | 0.0240 | 0.0320 |
| weeks 1-2 | +0.1868 | +0.2101 | 0.0522 | **0.0166** |
| 2024 weeks 1-2 | +0.1643 | +0.2104 | 0.0633 | 0.0308 |
| overall | +0.1921 | +0.1960 | 0.0174 | 0.0162 |

Every `@priors` mean multiplier came off the clamp: rec_yards 1.200 → **1.168**,
receptions 1.200 → **1.151**, rush_yards 1.200 → **1.181**, anytime_td 1.186 →
1.163. The correction layer has authority over the residual again.

**Confidence bands, the number a user actually experiences.** 2025 week 1 alone,
against the settled weeks as the reference:

| Stated | week 1 actual | gap | weeks 3+ gap |
|---|---|---|---|
| 0.550 | 0.566 | +1.6 pts | +0.4 pts |
| 0.651 | 0.677 | +2.7 pts | +1.5 pts |
| 0.749 | 0.773 | +2.3 pts | +2.3 pts |
| 0.856 | 0.865 | **+0.9 pts** | −0.3 pts |

The pre-registered bar was the top band within a point, and week 1 meets it at
+0.9. Two things are worth saying precisely rather than rounding off:

- **The sign flipped.** Week 1 was overconfident (−3.3 pts at the top) and is now
  marginally *under*-confident. Understating a probability is the error to
  prefer, and its shape now matches the settled season's.
- **The pooled `wk1-2` figure is −1.7 pts, not +0.9.** Week 2 drags it. Week 1 is
  the deliverable — 29 August is a week 1 — so the week-1 row is the one the bar
  was written about, and the pooled row is reported here rather than quietly
  dropped.

**Weeks 3+ are untouched.** Not argued — checksummed. Every projected mean at
2025 w2, w3, w8, w14 and 2024 w8 is identical to nine decimals across the change,
and the reason is recorded alongside: the prior-season pool is only consulted
where the current-season one holds fewer than `MIN_BASELINE_SAMPLES` rows, and
the smallest current-season pool in any of those weeks is 226. The stratum
metrics for weeks 3+ do move a little (early +0.1885 → +0.1874, late +0.1960 →
+0.1971) purely because the calibration accumulator now carries different week-1
residuals, not because any settled projection changed.

**The opening board got smaller and more selective**: 2025 week 1 goes from 4,923
projections to 4,669. A higher baseline raises the usage floor with it, so 255
marginal players drop off. That is the intended direction — the floor exists to
match the population a book would price.

## 6. What did not improve, and is not hidden

**Anytime touchdown still adds almost nothing at the opening.** 2025 weeks 1-2:
skill +0.0065 (it was +0.0104 before the fix), against +0.0440 in weeks 3+ on
n=1,518. Phase 6a measured the prior season's scoring record as the signal that
best survives the cold start and this project built the prior-season goal-line
path on that basis; graded on its own probabilities, it does not carry the
opening board. Ranking power and calibrated probability are different claims and
6a measured only the first. The path still earns its place — without it the
market is absent from opening weekend entirely, and week 2 depends on it — but
the opening headline belongs elsewhere.

**Week 2 gave a little back**: ECE 0.0240 → 0.0320, skill unchanged. Its
projections are provably identical; what changed is the correction it inherits,
because the `@priors` bucket it shares with week-1 rows now measures a smaller
bias. Net across the two weeks is strongly positive (0.0522 → 0.0166) and this is
the smaller half of that trade.

**2024's opening weekends remain overconfident at the top** (−5.0 pts at 0.8+,
against +0.9 for 2025), even though they improved substantially in aggregate.
That is the configuration whose prior season is 2023 — no `targets`, no
play-by-play — so it carries no receiving or touchdown markets and its passing
and rushing markets rest on thinner priors. **2026 will run in the 2025
configuration**, whose prior season is complete. This is the reason 6a designated
2025 the primary evidence.

## 7. Markets, 2025 opening weeks

| Market | n | skill | ECE | wk3+ skill |
|---|---|---|---|---|
| receptions | 5,410 | +0.2593 | 0.0271 | +0.2714 |
| pass_completions | 1,200 | +0.2385 | 0.0272 | +0.1651 |
| rush_attempts | 2,618 | +0.2277 | 0.0429 | +0.1644 |
| pass_yards | 1,185 | +0.2169 | 0.0509 | +0.1725 |
| pass_attempts | 1,211 | +0.2154 | 0.0476 | +0.1541 |
| rush_yards | 3,391 | +0.2047 | 0.0443 | +0.1532 |
| rec_yards | 5,471 | +0.1770 | 0.0264 | +0.1645 |
| pass_tds | 960 | +0.1730 | 0.0502 | +0.1931 |
| anytime_td | 1,518 | +0.0065 | 0.0647 | +0.0440 |

Every market except the two touchdown ones now grades at or above its
settled-season skill. Receptions and receiving yards are the best-calibrated and
the highest-volume, and they are what the opening board should lead with.

## 8. Transfers

The phase-6 plan named a narrower opening board — returning starters only — as
the fallback if the numbers disappointed. **The data ruled that out before the
fix and still rules it out after.** Changed-team rows are not the weak half:

| 2025 opening weeks | skill | ECE |
|---|---|---|
| same team | +0.2137 | 0.0254 |
| changed team | +0.2020 | 0.0190 |

`CHANGED_TEAM_PRIOR_MULTIPLIER` shrinks a transfer's projection harder toward
the baseline, which costs a little discrimination and buys calibration. Cutting
transfers would remove the better-calibrated half of the board.

## 9. The population

2025 week 1 publishes 4,669 projections across 852 players — against 3,086
player-markets and 844 players at week 3. The opening board is not a thin version
of the settled one.

The ceiling from 6a is unchanged and is not a modelling problem: only 70-74% of
players who produce in week 1 have any prior season at all. The board should be
visibly incomplete rather than quietly so.

---

## Recommendation

1. **Publish both opening weekends.** Weeks 1-2 are the best-calibrated stratum
   of the season and carry its highest skill.
2. **Lead with receptions, receiving yards and rushing volume.** They grade at or
   above their settled-season skill and are the best calibrated.
3. **Do not lead week 1 with anytime touchdown.** Keep the market — the board is
   incomplete without it and week 2 depends on it — but it adds almost nothing
   on opening weekend.
4. **Do not narrow the board by transfer status.** It would remove the
   better-calibrated half.
5. **Say on the board that week 1 is a priors-only projection**, that no matchup
   adjustment exists yet (`defense_position_ratings` has no week-1 rows), and
   that roughly a quarter of opening-weekend production comes from players no
   model can reach.
6. **Re-check the top confidence band once 2026 week 1 is graded for real.** The
   +0.9 pt figure rests on one season in the configuration 2026 will run in.

**Still blocking 29 Aug independently of all this:** `player_team_seasons` holds
0 rows for 2026, re-probed 2026-08-04 against 15,601 rows for 2025. No roster, no
board, however well it grades.
