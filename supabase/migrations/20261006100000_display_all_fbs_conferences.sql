-- =============================================================================
-- 0081 — Display every FBS conference, not just five
-- =============================================================================
-- THE CLIENT'S CHANGE (2026-10-06). The seed (0009) displayed SEC, Big Ten,
-- Big 12, ACC and American Athletic, per CLAUDE.md §4. The client reported "a
-- game tonight not showing": Southern Miss @ Troy (Sun Belt) had 96 props in
-- the database and was hidden only by this flag. The whole Tuesday-Thursday
-- midweek slate is Group of Five, so the board was empty on those nights. He
-- wants all FBS shown.
--
-- DISPLAY ONLY, as before. Ingest always covered all FBS (CLAUDE.md §4), so
-- every row these conferences need already exists; this changes what the
-- board, games list, cheat sheets and /no-vig show, and nothing upstream.
--
-- THE SIX, BY NAME AND NOT BY classification = 'fbs'. The conferences table
-- also holds CFBD's historical FBS conferences (Pac-10, Big 8, Southwest,
-- Skyline, ...), all classified 'fbs'. None has a current team, but a blanket
-- update would put dozens of dead names in the conference filter. FCS and
-- below stay off: an FCS visitor's props are not a market we model.
--
-- LOAD, MEASURED BEFOREHAND ON PRODUCTION as the anon role, cfb 2026 week 6:
-- board rows 3,313 -> 5,788 (every one of the six had rows that week). Warm
-- times did not move: count 223 -> 212 ms, first page 448 -> 303 ms, cheat
-- sheet 237 -> 256 ms, against the role's 3 s statement_timeout. The cost is
-- building the view, which happens either way, not the filter on it.
--
-- AN ADDITION, SO MIGRATION-FIRST IS SAFE: code already running reads this
-- flag from the data. The daily audit check "displayed conferences still
-- flagged" expects 5 until the matching worker commit is deployed, so apply
-- this and deploy that commit together.
--
-- Reversible: set the six back to false.
-- =============================================================================

update conferences
   set is_displayed = true
 where sport = 'cfb'
   and classification = 'fbs'
   and name in (
     'Sun Belt',
     'Conference USA',
     'Mid-American',
     'Mountain West',
     'Pac-12',
     'FBS Independents'
   );

comment on column conferences.is_displayed is
  'DISPLAY FILTER ONLY (CLAUDE.md §4). Controls which conferences appear in '
  'the UI conference filter and the default board/games/cheat-sheet scope. '
  'Ingest always covers every FBS team regardless of this flag — '
  'cross-conference games cannot be opponent-adjusted if one side is missing. '
  'Never reference this column in an ingest or feature query. Since 0081 '
  '(2026-10-06, the client''s change) every current FBS conference is '
  'displayed; CFBD''s historical FBS conferences and FCS and below are not.';
