-- =============================================================================
-- 0105 -- dispatch_slots: the merged Render services' record of what they ran
-- =============================================================================
-- Render ran this worker as twenty cron services, one per schedule. Every
-- push rebuilt all twenty (~20 build minutes, which hit the client's spend
-- cap on 2026-10-22) and each carries a $1 monthly minimum. They are merged
-- into three dispatcher services (odds, college daily, NFL daily) beside the
-- two that stay as they are (live scores, monitor). Each dispatcher runs the
-- old services' commands on the old schedules, from a table in
-- worker/worker/dispatch.py.
--
-- THIS TABLE IS HOW A DISPATCHER KNOWS WHAT IS DUE. A Render cron never runs
-- two of its own runs at once: it DELAYS the next run until the active one
-- ends (Render docs, cron jobs). So a run cannot tell from its own start time
-- which slot it is serving. Instead each run looks for every task's latest
-- scheduled occurrence that has no row here, claims it with an insert, and
-- runs it. The primary key makes the claim at-most-once: two runs that
-- overlap (a delayed run, a manual trigger) cannot both buy the same odds.
--
-- `mode` separates the shadow period (the dispatcher records what it WOULD
-- run while the old services still do the work, to be compared with their
-- real runs) from live running, so shadow rows never mark a slot as done.
--
-- Closed table, like pipeline_runs: RLS on, no policy, nothing granted to the
-- web's roles. Only the worker (database owner) reads or writes it.
-- =============================================================================

create table dispatch_slots (
  task          text        not null,
  slot_at       timestamptz not null,
  mode          text        not null check (mode in ('shadow', 'live')),
  service       text,
  claimed_at    timestamptz not null default now(),
  started_at    timestamptz,
  finished_at   timestamptz,
  exit_code     integer,
  note          text,
  primary key (task, slot_at, mode)
);

comment on table dispatch_slots is
  'One row per scheduled task occurrence a merged Render dispatcher claimed (worker/worker/dispatch.py). The primary key makes each occurrence run at most once. mode=shadow rows record what would have run while the old services still ran it.';

-- The dispatcher reads one task's latest claimed slots; the primary key's
-- leading column serves that. This index serves the review queries by time.
create index dispatch_slots_slot_at on dispatch_slots (slot_at desc);

alter table dispatch_slots enable row level security;
revoke all on table dispatch_slots from anon, authenticated;

-- Which dispatcher groups are switched on, and since when. Absent group = off.
-- {"odds": {"mode": "shadow", "since": "2026-10-27T17:00:00Z"}, ...}
-- `since` bounds catch-up: a group never runs or records an occurrence from
-- before it was switched to its current mode.
insert into app_config (key, value, description) values
  ('dispatch_mode', '{}'::jsonb,
   'Merged Render dispatchers (worker/worker/dispatch.py): per group {"mode": "off"|"shadow"|"live", "since": ISO time}. Absent = off. shadow records what would run without running it.')
on conflict (key) do nothing;
