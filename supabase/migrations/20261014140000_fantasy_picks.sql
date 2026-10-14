-- Fantasy game model picks: two more engines on game_picks (phase F3, 2026-10-14).
--
-- The client's NFL fantasy game model (his Google Sheet's formula, migration
-- 0094) is graded the way his rule plays are: a play wherever its number sits
-- far enough from the book's line, his own thresholds,
--
--   spread  4+ points between the model's home spread and the book's
--   total   6+ points between the model's total and the book's
--
-- made ONCE per game and market, by `nfl_fantasy_picks`, in the hour and a
-- half before kickoff, against the closing capture (Pinnacle, then
-- DraftKings, then FanDuel). Frozen at kickoff by 0074's trigger like every
-- pick, and readable once the game starts by 0090's policy, which is not
-- engine-specific. No moneyline picks: the user approved spread and total.
--
--   fantasy        the FIXED version, the one the game page shows by default
--   fantasy_sheet  his sheet exactly as it is, graded beside it, so the client
--                  can see which of the two holds up
--
-- model_prob is his own 0.23-per-point logistic (the slope of his win %)
-- applied to the points past the line. It is HIS scale, not a calibrated
-- probability; selection is by points (gap), as he does it.

alter table game_picks
  drop constraint game_picks_engine_known;
alter table game_picks
  add constraint game_picks_engine_known
  check (engine in ('v1', 'v2', 'rules', 'fantasy', 'fantasy_sheet'));

comment on column game_picks.engine is
  'Which pick rule made this row. v1: raw model probability, edge >= 5% (the shadow test since 2026-09-24). v2: the calibrated probability the games table shows (CALIBRATION_SLOPE), edge >= 2%, from 2026-10-08. rules: the client''s line-gap rules (spread 4+ points, total 6+, moneyline: model 60%+ on a favourite at -150 to -101, recorded at a win % blended toward the book), from 2026-10-09. fantasy / fantasy_sheet: the client''s NFL fantasy game model, fixed and as-is, spread 4+ and total 6+ points off the closing line, one pick per game and market, from 2026-10-15. A pick stands until the same engine switches side; each engine is graded on its own newest row made before kickoff.';
