-- =============================================================================
-- rank_vs_position now counts 1 = BEST defense (allows the least).
-- =============================================================================
-- It previously counted 1 = allows the MOST, chosen because the product's
-- headline use is a "who to target" list where the softest defense leads.
--
-- That cost more than it saved. Every surface showing a rank had to explain the
-- convention before the number meant anything, and it was still misread: given
-- the rule stated in capitals directly above the number, Gemini described rank
-- 118 of 136 as "a favorable ground matchup". A convention that needs
-- explaining everywhere it appears is the wrong convention.
--
-- NO DATA IS MIGRATED HERE. The ranks are DERIVED, so they are recomputed by
-- `python -m worker.jobs.build_splits` rather than arithmetically flipped in
-- place. Flipping would look equivalent and is not: ranks are dense 1..N within
-- each (season, as_of_week, position_group) cut, and an N that differs per cut
-- makes "N + 1 - rank" a different operation per group — exactly the kind of
-- silently-wrong transformation this project keeps finding. Recompute from the
-- adjusted figures, which are unchanged.
--
-- Consumers that want the softest defenses now sort DESCENDING: the weekly
-- targets panel and the board's matchup sort.
-- =============================================================================

comment on column defense_position_ratings.rank_vs_position is
  'Rank among FBS defenses on the adjusted figure at this cutoff, where 1 = ALLOWS THE LEAST (the best defense) — the conventional reading. A HIGH rank is the soft matchup, so "who to target" surfaces sort descending. Backs the opponent-rank sort and the defense-rank filter in CLAUDE.md §7. Derived: recomputed by build_splits, never edited in place.';
