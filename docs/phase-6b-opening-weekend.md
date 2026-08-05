# Phase 6 — the opening weekends: graded, published, labelled, checked

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

---

# Phase 6c — publishing them

The week floor is gone from `run_projections.projectable_weeks`. Eligibility is
now entirely a per-player question in `is_projectable`, so a week with nobody
eligible produces nothing — a fact about the roster rather than a rule about the
calendar. 2025 projects end to end, weeks 1 through 16, 81,198 rows:

| | wk1 | wk2 | wk3 | wk8 | wk14 |
|---|---|---|---|---|---|
| projections | 4,669 | 4,534 | 3,985 | 6,466 | 8,125 |
| players | 1,161 | 1,158 | — | 1,725 | — |

**The opening board is not the thin one.** Week 1 publishes more rows than week
3, because a full prior season admits more players than two current-season games
do.

## The deliverable's own claim did not survive contact

Phase 6's plan promised "p10-p90 visibly wider than later weeks". Compared in
aggregate that is simply false — week 1's relative width is 0.87x to 1.10x week
8's, *narrower* for rushing yards and passing volume.

It is a population artefact, and pairing the same player-market across both
weeks says so:

| Market | n | wk1 | wk8 | ratio |
|---|---|---|---|---|
| pass_yards | 123 | 2.077 | 1.680 | **x1.38** |
| pass_completions | 124 | 1.598 | 1.447 | x1.20 |
| rush_attempts | 273 | 1.979 | 1.834 | x1.13 |
| rec_yards | 716 | 3.665 | 3.711 | x1.11 |
| receptions | 519 | 2.011 | 1.878 | x1.10 |
| rush_yards | 375 | 3.543 | 4.024 | x1.00 |

For the same player the week-1 band **is** wider. The aggregate hides it because
week 8 admits ~560 more players, and the ones only a settled season qualifies are
marginal ones whose relative spread is large.

**And the widening should not be forced beyond this.** Weeks 1-2 grade as the
best-calibrated stratum of the season and their top confidence band is slightly
*under*-confident. A band widened to look appropriately uncertain would be a
worse band. The honest signals for the UI are the two the model already stores,
where the difference is unmistakable:

| paired, n=3,285 | week 1 | week 8 |
|---|---|---|
| `prior_weight` | 0.416 | 0.194 |
| `effective_sample` | 3.83 | 6.61 |

**That is what Phase 6d should surface** — "this rests on last season, 3.8 games
of effective evidence" — rather than a bar width that is doing its job quietly
and correctly.

## The alert that could not fire

`check_data_freshness` asked whether an EARLIER WEEK OF THE SAME SEASON had
produced projections. Week 1 has no earlier week, so an empty opening board —
the exact thing the client rejected, behind a `run_projections` that exits 0
having genuinely succeeded at projecting nobody — was invisible to every check
the monitor runs.

Week 1 now compares against the same week of the prior season. This is not
hypothetical: **an empty 2026 week 1 fires a critical alert from 8 August**,
because 2025 week 1 holds 4,669 projections and 2026 has no roster to build one
from. A first season with no predecessor still stays quiet, so the check remains
self-calibrating rather than acquiring a hardcoded floor.

*(This paragraph said "today" until Phase 6e checked it. `Slate.in_season` gates
every freshness check to within `IN_SEASON_WINDOW` — 21 days — of a kickoff,
which exists so a dormant February does not send a stale-data alert every day
for seven months. 2026 week 1 kicks off 29 Aug 16:00 UTC, so the check begins
running on **8 Aug 16:00 UTC** and was skipping, not passing, when 6c claimed
otherwise. Verified by evaluating `check_data_freshness` against the real
database at six moments either side of that boundary: silent before, CRITICAL
`empty-board` after.)*

## What needed no change

`v_slate_weeks`, which drives the week selector, was built over `projections`
rather than `games` so it could never offer a week that renders empty. It picked
up weeks 1 and 2 with no code change. Migration 0018's header claim that "the
first projectable week of a season is week 3" is now false and is corrected by
migration 0027 on the view's own comment, where a reader inspecting the schema
will meet it.

---

# Phase 6d — saying so on the board

Phase 6c ended by naming the two stored quantities the UI should surface:
`prior_weight` (0.416 at week 1 against 0.194 at week 8) and `effective_sample`
(3.83 against 6.61). Building it revealed that **only one of them is safe to
show, and it is not the one the phase brief led with.**

## The prior share inverts, and it inverts hardest where it matters

`prior_weight` does not split last season against this season. It splits a
player's **own** prior season against a generic **position baseline**. A player
who changed team takes `CHANGED_TEAM_PRIOR_MULTIPLIER`, so on 2025 week 1:

| | prior weight | what carries the rest | effective sample |
|---|---|---|---|
| returning starter | 0.50 | position baseline | 6.0 |
| transfer | **0.25** | position baseline | **3.0** |

Neither has played a game. Both are projected entirely from last season. But the
transfer scores *lower* on "share carried by last season" — because three
quarters of his projection is replacement-level baseline rather than anything
anyone watched him do. On a card that reads as *better* grounded. It is the
opposite.

**The failure was caught by a test, not by review.** The first assertion written
for `evidenceFor` was that an opening-weekend transfer and an opening-weekend
returning starter are both "priors-led", since neither has played. It failed at
0.25 < 0.40, and the failure was not a threshold to adjust — it was the quantity
being unfit for the purpose. The slate note being written at the time counted
rows by exactly that threshold and reported:

> 1,979 of 3,068 projections rest mostly on last season

when the true figure is **all 3,068**. The 1,089 it silently omitted were the
transfers: the least-evidenced third of the board, excluded from the count of
thinly-evidenced rows.

`effective_sample` cannot invert. 3.0 against 6.0, and less is always less. So
the board counts and prints that, everywhere; the share appears only on the
player page, next to the sentence that keeps it honest.

## What ships

**A per-card evidence pill.** `3.0 GM`, amber under four effective games,
muted above, with the full sentence on hover. It renders in **every** week, not
just the opening ones — the threshold separates players *inside* a week (59% of
week-1 rows are thin, 24% of week 8, 18% of week 14), so it is a fact about a
player rather than a warning label on a slate. TJ Lateef shows 2.0 gm in week 8.

**A slate note, driven entirely by the week on screen.** No function in
`lib/core/evidence.ts` asks what week it is. Counted over 2025's displayed
conferences, the note fires on exactly weeks 1 and 2:

| | rows | thin | thin % | ranked | note |
|---|---|---|---|---|---|
| wk1 | 3,068 | 1,829 | 60% | **0** | **Opening weekend** — full copy |
| wk2 | 3,041 | 1,831 | 60% | 1,122 | **Thin evidence** + partial ratings |
| wk3 | 2,543 | 1,268 | 49.9% | 2,008 | silent |
| wk8 | 3,848 | 918 | 24% | 3,848 | silent |
| wk14 | 4,868 | 887 | 18% | 4,868 | silent |

Week 1 gets all three claims: the thin count, that **no defense in the league
carries a rating** so nothing is matchup-adjusted and the opponent-rank filter
has nothing to select on, and that players with no prior season — about a
quarter of opening-weekend production — cannot be projected at all and are
absent. Week 2 gets the first two, correctly weakened: 1,122 rows *are* rated by
then, so the claim that nothing is adjusted must not be made.

**Week 3 sits at 49.86%, one row from the threshold.** That is left alone rather
than tuned. If it flips on, the note reports two true counts and nothing else —
the strong claims are gated on `openingWeekend`, which also requires the ratings
to be missing, and week 3's are present. The threshold decides tone, not truth.

**The defense panel says the real reason.** Its unrated state used to read "too
few games behind the week, or a non-FBS opponent". When *no* defense in the
league is rated it now says so, which is the week-1 truth and not that one.

## Schema and verification

Migration **0028** adds `effective_sample` to `v_board_rows` — written on every
projection since Phase 4a and read by nobody, because the view never selected
it. Column comments on both evidence columns record that they are **player-level**
(identical across a player's markets, which is why the card renders them once in
its header) and that the share must never be read alone.

Three audit checks now guard what the board renders: both columns present on all
81,198 projections, `effective_sample` inside the range a game count can occupy
(1.00 to 13.63 today), and `prior_weight` a share of one. **163/163.**

Verified: 98 web tests, 708 pytest, ruff and eslint clean, `check:schema` at 45
columns, a production build, and the board itself fetched on 2025 weeks 1, 2, 3
and 8 plus two player pages. **6/6 guard breaks replayed** — including one that
confirmed a slate whose ratings vanish in November still speaks up.

---

# Phase 6e — checking the phase's own claims

The standing rule at a phase boundary is to run everything and extend
`audit_data` rather than to re-read the diff. This one checked the claims 6b,
6c and 6d made, and three of them did not survive as written.

## 1. The alert does not fire today

6c wrote that an empty 2026 week 1 "fires a critical alert today". It does not.
`Slate.in_season` gates every freshness check to within `IN_SEASON_WINDOW` — 21
days — of a kickoff, which exists so a dormant February does not send a
stale-data alert every day for seven months. 2026 week 1 kicks off **29 Aug
16:00 UTC**, so the check begins running on **8 Aug 16:00 UTC** and was
*skipping*, not passing, when the claim was made.

Verified by evaluating `check_data_freshness` against the real database at six
moments either side of that boundary:

| moment | slate | result |
|---|---|---|
| today (5 Aug) | `in_season=False` | skipped |
| 8 Aug 15:59 | `in_season=False` | skipped |
| **8 Aug 16:01** | `in_season=True` | **CRITICAL `empty-board`** |
| 25 Aug (kickoff week) | `in_season=True` | CRITICAL `empty-board` |

The mechanism is right and the lead time is ample — three weeks before kickoff,
against a roster blocker that needs days. Only the sentence was wrong.

## 2. The audit was re-deriving a superseded run

`backtest_predictions` held 340,050 rows from **1 Aug**, before the Phase 6b
fix and before weeks 1-2 were ever graded — `min(week) = 3`. Every "re-derive
the deliverable from raw rows" check in the P3 group was therefore validating a
model that no longer ships, and passing, because those checks compare a run
against itself.

Re-walked 2025 with `--persist-predictions`: **186,515 predictions, weeks 1-16**.
The opening stratum now re-derives in SQL, sharing no code with the Python that
produced the metrics:

| stratum, re-derived | n | Brier skill |
|---|---|---|
| `wk1-2 opening` | 22,964 | **+0.2074** |
| `wk3-6 early` | 48,267 | +0.1855 |
| `wk7+ late` | 115,284 | +0.1910 |

**Phase 6b's central claim reproduces**: the opening weekends carry the season's
highest skill, computed from `model_prob_over` and `outcome_over` alone.

## 3. But the CALIBRATION does not reproduce — and that is a real finding

The same run puts the opening stratum at **ECE 0.0396, the worst of the three**,
against the 0.0184 that made it the best in 6b. P(over) runs **3.4 points low**
across weeks 1-2 (predicted 0.4062, observed 0.4402) where weeks 3-6 sit at +0.4
and week 7+ at −1.7.

The cause is structural, not a regression. **The correction layer is fitted
point-in-time from earlier data in the same walk, and its `priors` history
bucket only ever fills in the opening weeks — which happen once per season.** A
walk over one season has nothing to fit that cell on, so weeks 1-2 come out
carrying the raw bias that 6b reduced at source but did not eliminate.

Nothing about that is visible in the output. The walk succeeds, every number
prints, and the opening stratum still shows the season's highest skill. So it is
now stated three ways: a `log.warning` when a single-season walk grades the
opening weeks, a report caveat that appears only under that condition, and two
tests pinning both branches.

**Operationally: never quote opening-weekend calibration from a single-season
walk, and make sure the 2026 pipeline walks 2024-2025-2026 rather than 2026
alone.**

## 4. An audit check that was green and wrong for two phases

`no stored prediction was graded before the model could see 2 games`
(`as_of_week >= 2`) encoded the universe rule Phase 6b replaced. It kept
asserting the old rule through 6b, 6c and 6d without failing once — because the
only table that could contradict it is written by an optional flag, and no run
holding weeks 1-2 had ever been persisted. It fired the moment one was, on
12,031 legitimately graded opening-week rows.

Rewritten to state the current rule. The lesson is recorded in the file: **an
audit check over optionally-written data can be stale and green at the same
time.**

## 5. `projectable_weeks` had no test at all

Four lines of SQL, and since 6c removed the week floor, `season_type =
'regular'` is the **only** thing keeping bowls off the board — where previously
a mislabelled postseason game at week 1 was excluded twice over. That matters
because CFBD did once number a December bowl as week 1, and that bug produced
real lookahead into every earlier week.

Three tests now cover it, and **3/3 guard breaks replay**: re-adding a week
floor, dropping the `season_type` predicate, and excluding one ordinary week are
each caught.

*(The first replay run reported 3/3 MISSED. The harness was invoking the system
Python rather than the venv, so pytest never ran. An all-miss replay is more
likely a broken harness than three broken tests — check the exit code before
believing the result.)*

## What the audit covers now

**168 checks, all passing**, including a new `P6 opening weekend` group:

- every published projection clears the universe rule, re-derived in SQL from
  `player_game_stats` — 21,638 player-weeks, 0 inadmissible, 1,464 of the 2,319
  opening-week ones standing on priors alone
- no season the board covers is missing its opening weekend (the fault the
  phase exists to prevent, as data rather than as a claim)
- no projection was built with knowledge of its own week — 81,198 rows, 0
  violations, and every week-1 row at `as_of_week = 1`
- **a week-1 team rating precedes week 1 rather than following it.** Elo is the
  one feature a week-1 board takes from the current season, and a snapshot
  labelled "week 1" that described the state *after* week 1 would be lookahead
  into the exact games being predicted. Falsifiable rather than trusted: 90 of
  133 teams that played in week 1 have a different rating at week 2
- the walk grades the opening weeks the board publishes — `first_week = 1`

## The rest of the sweep

Numbers in this document were re-derived from the database and match exactly:
81,198 projections across 16 weeks; weeks 1/2/3/8/14 at 4,669 / 4,534 / 3,985 /
6,466 / 8,125 projections and 1,161 / 1,158 / 1,016 / 1,725 / 2,240 players.

**713 pytest, 98 web tests, ruff and eslint clean, `check:schema` at 45 columns,
`audit_data` 168/168.** Key hygiene re-checked: no tracked file carries a
credential-shaped literal, only the placeholder in `.env.example`.

**One thing for the client rather than the code: the database is now 448 MB**,
up from 402 MB, because this phase persisted a second full set of predictions.
Supabase's free tier stops at 500 MB. Nothing was deleted to make room — the
superseded runs are evidence — but the next persisted walk needs either a paid
tier or a decision about which runs to drop.
