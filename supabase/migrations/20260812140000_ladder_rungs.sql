-- =============================================================================
-- 0039 -- the alternate-line ladder
-- =============================================================================
-- The client's ask, in his words: "RB line 60, model says 90." One probability
-- against one book line does not answer the question he is actually asking,
-- which is how far the line can be pushed before the model stops agreeing.
--
-- WHY THIS IS STORED RATHER THAN COMPUTED ON READ. `projections` already holds
-- family + params, so P(over X) at any threshold is free -- but only where the
-- distribution maths lives, and that is scipy in the worker. Three of the seven
-- families in live use (gamma, negative_binomial, beta_binomial) need the
-- regularised incomplete gamma and incomplete beta functions; `rec_yards` alone
-- is fitted as gamma on some rows and lognormal on others, so a reader cannot
-- even assume a market has one family. Computing rungs in SQL is not possible
-- for those families, and computing them in the browser would mean a THIRD
-- implementation of the model maths -- after Python and the plpgsql de-vig twins
-- that `core/probability.py` already warns must be kept in lockstep -- in the
-- numerically hardest cases, in the layer the client sees. Precomputing keeps
-- scipy the single source of truth.
--
-- WHY THE RUNGS ARE ANCHORED ON THE PROJECTION, NOT THE BOOK LINE. College books
-- post props late, often Thursday or Friday for a Saturday game (CLAUDE.md §7),
-- while projections exist from Monday. A ladder centred on the book line could
-- not be computed until the line landed, which would make the feature useless
-- for most of the week -- the precise thing the late-line requirement exists to
-- avoid. Rungs sit on the market's own grid and are windowed to the player's
-- distribution, so they are valid before any line exists and stay valid when one
-- arrives; the UI marks where the book sits among them.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- markets.ladder_step
-- -----------------------------------------------------------------------------
alter table markets
  add column ladder_step numeric(5, 2);

comment on column markets.ladder_step is
  'Spacing between ladder rungs, in this market''s own unit. A column rather than a code branch, matching this table''s rule that adding a market is a row (see the table comment). NULL means this market has no ladder: anytime_td is a single probability by construction, so a ladder of it would be the same number repeated. Values are sized off the measured spread of live projections, not guessed -- the worker widens the step by an integer factor when a distribution is too broad to cover in the rung cap, so this is the FINEST spacing a market will show, not the only one.';

update markets set ladder_step = 25 where key = 'pass_yards';
update markets set ladder_step = 1  where key = 'pass_tds';
update markets set ladder_step = 5  where key = 'pass_attempts';
update markets set ladder_step = 5  where key = 'pass_completions';
update markets set ladder_step = 10 where key = 'rush_yards';
update markets set ladder_step = 2  where key = 'rush_attempts';
update markets set ladder_step = 1  where key = 'receptions';
update markets set ladder_step = 10 where key = 'rec_yards';
-- anytime_td deliberately left NULL; see the column comment.

-- A binary market has nothing to ladder, and a non-binary one that somehow lost
-- its step would silently render an empty panel rather than fail. State the
-- invariant so a future market has to make the decision explicitly.
--
-- THE STEP MUST BE A WHOLE NUMBER, and that is not fussiness. Rungs are placed at
-- `k * step + 0.5` so that every line is a half-integer, which is how books post
-- and which removes the push case outright -- `prob_over` treats a whole-number
-- line as a strict inequality, so an integer rung would quietly exclude the tie
-- from both sides. A fractional step breaks that: with a step of 2.5 the grid
-- runs 0.5, 3.0, 5.5, 8.0, landing on whole numbers every other rung. The column
-- stays numeric rather than integer because the units are a market's own and
-- there is no reason to promise they are always countable, but the values that
-- produce a coherent ladder are.
alter table markets
  add constraint markets_ladder_step_matches_binary
  check (
    (is_binary and ladder_step is null)
    or (
      not is_binary
      and ladder_step is not null
      and ladder_step > 0
      and ladder_step = trunc(ladder_step)
    )
  );

-- -----------------------------------------------------------------------------
-- projections.ladder
-- -----------------------------------------------------------------------------
alter table projections
  add column ladder jsonb;

comment on column projections.ladder is
  'Alternate-line rungs for this distribution: a JSON array of {"line": numeric, "prob_over": numeric} ascending by line, or NULL for a market with no ladder_step and for rows projected before migration 0039. Written by run_projections from the SAME calibrated distribution the stored quantiles describe, so a rung can never disagree with p50 -- which it could if it were derived separately. prob_over is the raw model probability and carries no book comparison: the edge against a real price is the pick''s job, and a rung is not a priced market.';

-- Cheap shape guard. Deliberately only checks the outer type: validating every
-- element on every insert would cost more than it protects, and the worker
-- already validates params against the declared family before it can produce a
-- rung at all.
alter table projections
  add constraint projections_ladder_is_array
  check (ladder is null or jsonb_typeof(ladder) = 'array');
