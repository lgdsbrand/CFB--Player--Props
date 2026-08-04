# Phase 6b — grading the opening weekends

**The review gate.** Weeks 1 and 2 had never been scored by any walk
(`MIN_BACKTEST_WEEK` was 3), so "can the board open with the season" had no
numeric answer. It has one now. Run `d606279b`, 2026-08-04, 360,725 graded
predictions across 2024 and 2025.

Companion documents: [phase-6a-early-season-ceiling.md](phase-6a-early-season-ceiling.md)
measured the ceiling before any of this was graded;
[calibration-report.html](calibration-report.html) is the rendered report this
summarises.

---

## The answer

**Ship week 2 as it stands. Do not ship week 1 until its overconfidence is
fixed, and that fix is a specific one rather than a scope cut.**

The opening weekends discriminate nearly as well as the settled season. What
they do not do yet is state honest probabilities, and the failure is confined to
week 1 and to the top of the confidence range — which is exactly where a user
acts.

---

## 1. The phase table

| Stratum | n | Brier skill | ECE |
|---|---|---|---|
| `wk1-2 opening` | 39,638 | +0.1778 | **0.0558** |
| `wk3-6 early` | 95,021 | +0.1885 | 0.0182 |
| `wk7+ late` | 226,066 | +0.1960 | 0.0243 |
| overall | 360,725 | +0.1921 | 0.0174 |

Skill holds up: the opening weeks reach 91% of what the late season manages.
**Calibration does not: 0.0558 is three times any other stratum.**

Note what the overall row does *not* say. Pooled ECE is computed on pooled bins,
so an overconfident group and an underconfident one partly cancel — the headline
0.0174 is *better* than the previous run's 0.0184 while containing a stratum at
0.0558. That cancellation is the entire reason weeks 1-2 are reported apart.

**Weeks 3-16 moved, as expected and as agreed.** They are graded with a
calibration accumulator that now carries two more weeks of residuals and a
separate `priors` bucket, so the numbers are not identical to the previous run:
overall skill +0.1934 → +0.1921, ECE 0.0184 → 0.0174. The early stratum improved
on both counts (+0.1850 → +0.1885, ECE 0.0218 → 0.0182); late gave back a little
(+0.1968 → +0.1960, ECE 0.0229 → 0.0243). The population of weeks 3+ is
unchanged — same players, same markets, same rows.

## 2. It is week 1, not "the opening weeks"

2025 only, which [phase-6a](phase-6a-early-season-ceiling.md) established as the
primary evidence because 2024's prior season carries no `targets`:

| | n | Brier skill | ECE |
|---|---|---|---|
| week 1 | 12,506 | +0.1611 | **0.0780** |
| week 2 | 10,933 | **+0.2130** | 0.0240 |
| weeks 3+ | 163,551 | +0.1945 | 0.0163 |

**Week 2 grades better than the season it opens.** One completed game is enough:
skill above the full-season figure, ECE in the normal range. Nothing about week 2
needs fixing or hedging.

Week 1 is where the cold start actually bites. 2024's opening weeks are worse
again (+0.1643, ECE 0.0633) and carry no receiving or touchdown markets at all,
which is the missing-`targets` hole rather than a modelling result.

## 3. What "overconfident" means here, concretely

2025 opening weeks, by stated confidence:

| Stated | n | Model says | Actually happened |
|---|---|---|---|
| 0.50-0.60 | 5,746 | 0.550 | 0.554 |
| 0.60-0.70 | 5,774 | 0.650 | 0.643 |
| 0.70-0.80 | 5,792 | 0.750 | **0.729** |
| 0.80+ | 6,127 | 0.870 | **0.837** |

The coin-flip end is honest. The confident end is overstated by two to three
points — and a props board is read from the confident end down.

## 4. The mechanism, and why it is fixable at source

The calibration snapshot names it. In the new `priors` bucket the **mean**
multiplier is pinned at the clamp:

| Market | `@priors` mean multiplier | n |
|---|---|---|
| rec_yards | **x1.200** (= `MAX_MEAN_MULTIPLIER`) | 1,584 |
| receptions | **x1.200** | 1,642 |
| rush_yards | **x1.200** | 1,419 |
| anytime_td | x1.186 | 2,270 |

A binding clamp is not a correction, it is a report that the projection is
biased low by *more* than the correction layer is permitted to fix.
`MAX_MEAN_MULTIPLIER` exists precisely to say so: CLAUDE.md §6 treats a
projection wrong by more than a fifth as a modelling failure to fix at source
rather than paper over.

The likely cause is survivorship in the anchor. A week-1 projection and its line
both rest on the player's **full prior-season average**, and the players who
dress in week 1 are disproportionately those who *ended* last season with a role
— their full-season average is dragged down by the weeks they spent behind
someone. The base rate agrees: outcomes clear the line 46.6% of the time in week
1 against 43.0% from week 3 on.

`prior_{stat}_recent_pg` — the last five games of the prior season — is already
materialized in the feature frame and read by nothing, exactly as
`prior_{stat}_sd` was before 6b.1. Blending the anchor toward it is the obvious
first attempt and costs no new ingest.

## 5. Two things I expected that the grading contradicts

**Anytime touchdown does not carry the opening board.** 6a measured the prior
season's scoring record as the signal that survives the cold start best (AUC
0.52-0.72, higher for transfers than stayers at QB) and I built the
prior-season goal-line path on that basis. Graded on its own probabilities it
adds almost nothing at the opening — 2025 weeks 1-2 skill **+0.0104** on 1,687
predictions, against +0.0440 in weeks 3+. The difference is roughly one standard
error on that sample, so the honest reading is "no worse than usual, and usual is
weak", not "broken". Ranking power and calibrated probability are different
claims and 6a only measured the first. The path still earns its place — without
it the market is absent from opening weekend entirely, and week 2 depends on it
— but the board should not lead with it in week 1.

**Narrowing to returning starters would not help.** The phase-6 plan's stated
fallback, if the numbers disappointed, was a smaller opening board of players who
stayed at the same school. The data says the opposite: in the opening weeks
changed-team rows are the *better-calibrated* half.

| 2025 opening weeks | skill | ECE |
|---|---|---|
| same team (n 27,020 both seasons) | +0.1831 | 0.0591 |
| changed team (n 12,618 both seasons) | +0.1651 | 0.0505 |

Per market the pattern is consistent — rec_yards ECE 0.069 same vs 0.033
changed, receptions 0.063 vs 0.039, rush_yards 0.041 vs 0.017.
`CHANGED_TEAM_PRIOR_MULTIPLIER` shrinks a transfer's projection toward the
baseline, which costs discrimination and buys calibration. So the transfer
discount is not too generous in the sense 6a suspected, and cutting transfers
from the board would remove its best-calibrated rows.

## 6. Markets, 2025 opening weeks

| Market | n | skill | ECE | wk3+ skill |
|---|---|---|---|---|
| receptions | 5,472 | +0.2506 | 0.0482 | +0.2714 |
| rush_yards | 3,471 | +0.1894 | 0.0326 | +0.1531 |
| pass_yards | 1,209 | +0.1875 | 0.0591 | +0.1680 |
| rush_attempts | 2,654 | +0.1867 | 0.0798 | +0.1646 |
| rec_yards | 5,553 | +0.1643 | 0.0548 | +0.1645 |
| pass_tds | 945 | +0.1524 | 0.0806 | +0.1922 |
| pass_attempts | 1,227 | +0.1486 | 0.0822 | +0.1577 |
| pass_completions | 1,221 | +0.1159 | 0.1034 | +0.1660 |
| anytime_td | 1,687 | +0.0104 | 0.0622 | +0.0440 |

Receiving volume and rushing yards open strongest. The quarterback volume
markets are the weakest calibrated (`pass_completions` at ECE 0.103), which is
consistent with them being the markets whose mean correction was already largest.

## 7. What the population actually is

2025 week 1 grades 852 players and 3,169 player-markets, against 844 and 3,086 in
week 3 — the opening board is not a thin version of the settled one. 2024 week 1
grades 425 players, and the gap is entirely the receiving and touchdown markets
its prior season cannot supply.

The hard ceiling from 6a is unchanged and is not a modelling problem: only
70-74% of players who produce in week 1 have any prior season at all. The board
should be visibly incomplete rather than quietly so.

---

## Recommendation

1. **Publish week 2 with the rest of the season.** It grades better than the
   season average. No caveat is needed beyond the usual.
2. **Hold week 1 until the low bias is fixed.** Blend the week-1 anchor toward
   `prior_{stat}_recent_pg`, re-walk, and require the `@priors` mean multiplier
   to come off its clamp and the 0.8+ confidence band to land within a point.
   This is a narrow fix on a column already in the frame.
3. **Do not narrow the board by transfer status.** It would remove the
   better-calibrated half.
4. **Do not lead week 1 with anytime touchdown.** Keep the market — week 2 needs
   it and the board is incomplete without it — but the opening-weekend headline
   belongs to receptions, receiving yards and rushing yards.
5. **Say on the board that week 1 is a priors-only projection**, and that roughly
   a quarter of opening-weekend production comes from players no model can reach.

**Still blocking 29 Aug independently of all this:** `player_team_seasons` holds
0 rows for 2026. No roster, no board, however well it grades.
