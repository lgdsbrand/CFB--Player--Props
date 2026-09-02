-- =============================================================================
-- Turn the odds seam on: "none" -> "theoddsapi".
-- =============================================================================
-- `odds_adapter` has been "none" since it was seeded in 20260730100900, and that
-- was correct while CLAUDE.md §9.1 was open: it was unconfirmed whether the
-- client's Odds API plan covered NCAAF player props at all, so the seam shipped
-- as configuration and the board degraded to model leans.
--
-- THAT QUESTION IS NOW ANSWERED, ON THE LIVE API RATHER THAN FROM DOCS. Measured
-- 2026-09-01: the plan serves NCAAF player props. One 3 Sep event returned four
-- books, with FanDuel carrying pass yds, pass TDs, rush yds, rec yds and anytime
-- TD. Books had posted on a TUESDAY, not the Thursday/Friday CLAUDE.md §7
-- assumes — so the cost of leaving this off is not hypothetical, it is this
-- weekend's lines.
--
-- WHAT THIS COSTS, MEASURED RATHER THAN DERIVED. Billing is one credit per market
-- returned per event. An 8-event sample spread across the provider's 146-event
-- list cost 7 credits, mean 0.9/event, because most events return NOTHING and
-- therefore cost NOTHING: FBS-vs-FCS fixtures carry no props, and neither does
-- any game beyond the coming weekend. A full capture on 2026-09-01 is ~128
-- credits, rising through Thursday and Friday as more books post.
--
-- That is far cheaper than the estimate in render.yaml, which feared a full slate
-- would eat the monthly allowance in about a week. That figure assumed ~9 markets
-- on every event. The real distribution is a short head of marquee games and a
-- long tail of zeros, so THE SIX-HOURLY CADENCE IN render.yaml IS LEFT ALONE.
--
-- THE POOL IS SHARED AND IT DRAINS WHETHER WE RUN OR NOT. It is the same key
-- behind the client's MLB, tennis and WNBA models. In the first day of this
-- billing cycle 825 of 20,000 credits went while `odds_adapter` was still "none"
-- and this project had spent nothing — so the budget available here is well under
-- the headline cap, and the cap is still 20,000 because the tier bump discussed
-- with the client on 2026-08-27 has not landed. Watch `x-requests-remaining`
-- rather than assuming the whole pool.
--
-- TURNING IT BACK OFF IS THIS ONE ROW. "none" remains a supported product state,
-- not a broken one: `ingest_odds` exits 0 and says so, and the board shows leans
-- without lines. Nothing downstream needs redeploying to reverse this.
--
-- NO KEY GOES IN THIS TABLE. app_config is world-readable under RLS. ODDS_API_KEY
-- comes from the worker environment and nowhere else (CLAUDE.md §0). Selecting
-- the adapter here without that key set is a startup error in `ingest_odds`, by
-- design, rather than a silent no-op.
-- =============================================================================

insert into app_config (key, value, description) values
  ('odds_adapter', '"theoddsapi"'::jsonb,
   'Which odds adapter to run: "theoddsapi" or "none". Live since 2026-09-01, when the plan was confirmed on the API to serve NCAAF player props. "none" is a supported fallback — no lines are ingested and the board shows model leans only, degrading gracefully rather than breaking (CLAUDE.md §9.1). The API key lives in ODDS_API_KEY in the worker environment and never in this table.')
on conflict (key) do update
  set value = excluded.value,
      description = excluded.description,
      updated_at = now();
