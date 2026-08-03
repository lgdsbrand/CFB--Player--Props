-- =============================================================================
-- Phase 5d: where pipeline alerts go.
-- =============================================================================
-- CLAUDE.md §8 Phase 5 asks for monitoring and alerting on the pipeline. Which
-- channel that means is not settled with the client, so it is configuration
-- rather than code — the same shape as `odds_adapter` and `ai_adapter`.
--
-- DEFAULT IS 'log', AND UNLIKE THE OTHER TWO SEAMS THAT IS NOT AN OFF SWITCH.
-- `odds_adapter = "none"` is a real product state: the model still runs and the
-- board shows leans without lines (CLAUDE.md §9.1). There is no equivalent
-- state for alerting — "nobody is told when the pipeline breaks" is an outage
-- waiting to be discovered by a reader of the board, not a configuration
-- choice. So the weakest option here still writes the finding somewhere a human
-- can reach it, and `monitor_pipeline` additionally exits non-zero on anything
-- critical so Render's own cron-failure notification fires.
--
-- NO WEBHOOK URL GOES IN THIS TABLE. A Slack incoming-webhook URL carries its
-- secret in the path — it is a credential that happens to look like an address.
-- app_config is world-readable under RLS and there is an audit check for
-- credential-shaped values here. The URL comes from ALERT_WEBHOOK_URL in the
-- environment and nowhere else (CLAUDE.md §0).
-- =============================================================================

insert into app_config (key, value, description) values
  ('alert_adapter', '"log"'::jsonb,
   'Where monitor_pipeline sends findings: "log" or "webhook". Default "log" — findings go to the Render run log and a critical finding exits the cron non-zero so Render notifies. Set to "webhook" only once ALERT_WEBHOOK_URL is set in the worker environment; the URL is a credential and NEVER belongs in this table (CLAUDE.md §0).')
on conflict (key) do nothing;
