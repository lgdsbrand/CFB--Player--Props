-- =============================================================================
-- 0104 -- board_index: a week's board as ONE fixed statement
-- =============================================================================
-- WHY. The board pushed every filter into SQL, so every combination of
-- position, market, game, day, conference, ranked, side, edges, confidence,
-- opponent rank, books, sort and page was a different statement. PostgREST
-- prepares each statement it runs and keeps it on that connection; one of
-- these keeps 0.5-2.4 MB (it carries the whole expanded 13-join view), and on
-- 2026-10-24 production saw 474 of them in the 13 minutes after a restart.
-- That is the memory the instance kept running out of (see 0103).
--
-- WHAT. The week's rows reduced to the columns the board filters, sorts and
-- counts on, as one compact JSON array of arrays (~0.8 MB for a full college
-- week, under the Vercel data cache's 2 MB entry limit). The web caches it per
-- sport and week, applies the filters and the sort in TypeScript, and fetches
-- the wide columns only for the rows on the page, by id. Every filter
-- combination then costs the database the same three statements.
--
-- A function rather than a select through PostgREST because a select is capped
-- at 1,000 rows per response (a week is ~5,500), and a scalar json result is
-- not. STABLE and security invoker, so it reads exactly what anon could.
--
-- THE COLUMN ORDER IS A CONTRACT with `web/lib/data/board-index.ts`
-- (`INDEX_COLUMNS`). Append, never reorder.
-- =============================================================================

create or replace function board_index(p_sport sport, p_season integer, p_week integer)
returns json
language sql
stable
set search_path = public, pg_temp
as $$
  select coalesce(
    json_agg(
      json_build_array(
        b.projection_id,
        b.player_id,
        b.game_id,
        b.market_key,
        b.position_group,
        b.start_date,
        b.conference_name,
        b.conference_is_displayed,
        b.player_name,
        b.has_book_line,
        b.has_call,
        b.sportsbook_key,
        b.team_poll_rank,
        b.side,
        b.is_binary,
        b.edge,
        b.display_confidence,
        b.opponent_rank_vs_position,
        b.publishes_call,
        b.book_prob_over
      )
      order by b.projection_id
    ),
    '[]'::json
  )
  from v_board_rows b
  where b.sport = p_sport
    and b.season = p_season
    and b.week = p_week;
$$;

grant execute on function board_index(sport, integer, integer) to anon, authenticated;

comment on function board_index(sport, integer, integer) is
  'A week''s board rows reduced to their filter, sort and count columns, as a JSON array of arrays ordered by projection_id (migration 0104). One fixed statement per sport and week, so filter combinations stop multiplying prepared statements in PostgREST. Column order is a contract with web/lib/data/board-index.ts.';
