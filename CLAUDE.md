# CLAUDE.md — College Football Player Prop Model

This file is the project brief and working agreement for this repo. Read it fully
before writing any code. This is the **college football** build; NFL reuses this
project's core with a different data adapter.

> **Superseded 2026-08-11 — there is no separate NFL repo.** This line originally
> read "a separate NFL repo reuses this project's core". The client chose **one
> app with a sport toggle**, so NFL is a second `sport` in *this* repo and *this*
> database, not a second product. **See the amendment in §3 for the whole
> decision and its reasoning** — do not start an NFL build by copying this repo
> into a second folder.

---

## 0. How to work in this repo (read first)

- **Do not build the whole thing in one pass.** Work in the phases in section 8.
  At the start of each phase, propose a short plan and wait for my go-ahead before
  writing code.
- After each phase, stop and let me review. The schema and the backtest report get
  reviewed **before** any UI is built.
- Prefer small, verifiable steps. Write the migration, show me the tables. Write the
  ingest, show me row counts. Fit the model, show me the calibration numbers.
- Ask before adding a dependency that isn't already justified below.
- Never commit secrets. All keys come from environment variables. Assume this repo
  will be public-adjacent; treat key hygiene as a hard rule, not a nicety.

---

## 1. What we are building

A web tool that, for each relevant college football player each week, states whether
the model favors the **OVER or UNDER** on that player's book prop line, with a
**confidence percentage**. It does **not** display a projected stat line as the
headline number.

Why this matters and must not be "simplified away": the model still projects a full
outcome **distribution** internally. The over/under call and the confidence % are
**derived from** that distribution (the share of it that clears the line). The
projection is the engine; the over/under + % is the surface. Keep the projected range
available on the player detail view as secondary info, but never as the main claim.

Every market speaks the same language: a probability per pick. This includes
touchdowns, which are expressed as an **anytime-scorer probability**, never a
projected count.

---

## 2. Stack (fixed)

- **Frontend:** Next.js (App Router) + TypeScript, deployed on Vercel.
- **Styling:** Tailwind CSS. **Charts:** Recharts.
- **Database:** Supabase (Postgres). Use Row Level Security.
- **Data pipeline:** Python worker deployed on Render, run on a schedule (cron).
  It ingests data, computes projections, and writes results to Supabase. The
  Next.js app only ever reads from Supabase; it never calls data providers directly.
- **AI analysis:** an LLM writes a short read per player. Generated **once per week
  per player and cached in Supabase.** Never called per page view.
- **Source control:** GitHub, preview deploy per branch so changes are reviewable on
  a live URL before merge.

**Python libs:** polars for data, scikit-learn and statsmodels for modeling,
the official `cfbd` client for data. Justify anything beyond these before adding it.

---

## 3. Architecture: keep the sport seam clean

Structure the code so a second sport (NFL) can reuse most of it later. Two layers:

- **Sport-agnostic core** — the Next.js app shell and components, the modeling and
  backtest library, the projection→probability math, the Supabase schema and the
  read layer. Nothing here should hardcode college-only assumptions.
- **Sport-specific adapter** — CFBD ingest, opponent adjustment, conference filter,
  rating snapshots. This is the layer NFL will replace.

Do not build a shared package or workspace. Just keep the boundary disciplined so
the core stays cleanly separable.

*(As originally written this sentence ended "so the core can be copied into the
NFL repo cleanly." The amendment immediately below replaced the repo split with a
runtime `sport` dimension; the two-layer discipline it describes is unchanged.)*

> **Amended 2026-08-11 — the seam is now a runtime dimension, not a repo split.**
> The client chose **one app with a sport toggle** over two separate products.
> The two-layer discipline above is unchanged and still the point; what changed
> is where the boundary is enforced. NFL rows will live in *these* tables,
> separated by a `sport` column on `conferences`, `teams`, `games` and `players`
> (migration 0035), and every read that could mix the two filters on it
> (`web/lib/core/sport.ts`). Downstream tables inherit sport through their
> foreign keys and deliberately carry no copy of it.
>
> Splitting back apart later stays cheap and is the direction that works: the
> sports share no rows, so a split is a filtered copy, whereas merging two live
> databases would mean re-keying every primary key. One repo and one database can
> also serve two URLs as two Vercel projects, so "toggle" and "separate sites"
> are not opposed.

---

## 4. Data layer (CollegeFootballData)

- **Source:** CollegeFootballData (CFBD) via the official `cfbd` Python client.
  API key from env (`CFBD_API_KEY`). Note: the free tier rate-limits hard; a full
  multi-season backfill needs the paid tier. Assume the key provided is paid.
- **Ingest ALL FBS teams, not just the displayed conferences.** Cross-conference
  games are common, especially early season, and we cannot opponent-adjust a game if
  we lack one side's data. Conference selection is a **display filter only**, never a
  data cut.
- **Displayed conferences:** SEC, Big Ten, Big 12, ACC, American. (The Pac-12 is
  largely dissolved post-realignment; handle whatever teams remain gracefully rather
  than assuming a fixed six.)
- **Pull:** player game stats, play-by-play, team game results, rosters, betting
  lines where available, and venue/weather. Use CFBD's weather where present;
  Open-Meteo is an acceptable fallback for outdoor venues.
- **Team strength ratings:** ingest SP+, SRS, Elo, FPI from CFBD. These are **team
  level** and serve as priors and sanity checks only — they cannot tell us what a
  defense is bad against by position.
- **CRITICAL — no lookahead bias.** Store ratings and any derived team/defense
  metrics as **point-in-time weekly snapshots** (what was known *before* week N).
  Backtests and live predictions must only ever use data available at prediction
  time. Applying end-of-season ratings to earlier games is a silent, disqualifying
  bug. Design the schema so this is enforced, not just hoped for.

---

## 5. The position-split engine (core signal)

The primary defensive signal is **what a defense allows to each position**, computed
from play-by-play — not from any external rating and not from an API field (no
provider serves this cleanly; we build it).

- Attribute each play to the position of the player involved, group by defense and
  position and week, and aggregate. This produces "rush yds allowed to RBs",
  "rec yds allowed to TEs", etc.
- **Opponent-adjust everything.** Raw defensive numbers are misleading because
  schedules are wildly unbalanced (a unit that played three weak offenses looks
  elite). Adjust for opponent strength before these numbers feed a projection.
- These splits power the defensive ranks, the "defense detail" view, and the weekly
  targets section.

---

## 6. Modeling

- For each market, produce an **outcome distribution**, not a point estimate. The
  over/under call is the side the majority of the distribution falls on; the
  confidence % is the probability mass past the line.
- **Edge definition (match the client's existing MLB pitcher model exactly):**
  `edge = model probability − book implied probability`, where the book implied
  probability is **de-vigged** (strip the vig from the two-way price before
  comparing). Do not use a naive (projection − line) / line percentage. This keeps
  our numbers consistent with the pitcher-props model already on their site, which
  defines edge the same way. Support an edge threshold (their pitcher model uses
  ≥ 5%) and an "edges only" filter.
- **Markets:**
  - QB: pass yds, pass TDs, attempts, completions, rush yds
  - RB: rush yds, rec yds, rush att, receptions
  - WR / TE: receptions, rec yds
- **Anytime touchdown** = probability to score, modeled as (chance of reaching
  scoring situations, from goal-line usage) × (finish rate, from the player's own
  scoring rate and the defense's rate of allowing scores to that position). A
  Poisson/logistic formulation is appropriate. Output a single probability.
- **College-specific weighting:** roster turnover from the transfer portal and NIL
  means prior-season production often happened at another school. Down-weight
  prior-year data relative to current season; expect and surface wider uncertainty
  early in the season. TD probabilities especially should lean on priors early and
  sharpen as the season accumulates.
- **Backtest + calibration report (required deliverable of Phase 3).** Evaluate
  against actual outcomes across recent seasons, point-in-time only. Report
  calibration explicitly: when the model says 60%, does it hit ~60%? A confident,
  well-calibrated model matters far more than a flashy point projection. This report
  is reviewed before any UI work begins.

---

## 7. Application

**Main board:** the player-market list, styled like the client's pitcher-props board
(section 7, Design language). Each entry shows team, matchup, book line, the
**OVER/UNDER call**, the **edge %**, and per-book odds. Sortable by edge, hit rate
(L5), or opponent rank vs position. This is the screen people open, so it must read
fast and match the existing MLB board so the two feel like one product.

**Controls:**
- Position tabs: QB / RB / WR / TE
- Stat selector: per-position dropdown to switch the market shown
- Game selector and a player search bar
- Conference filter (the displayed-conferences list above)
- Filters: confidence threshold, defense-rank-vs-position, hit-rate window (L5 / L10).
  Home/Away splits are thin in college (neutral sites, uneven schedules) — include
  the control but treat it as secondary here; it's a first-class filter in the NFL build.

**Player detail (on row click):**
- Game log with a hit-rate chart: last-N games as bars against the line, over/under
  colored, line drawn as a reference. (See the prototype's L10 chart.)
- Hit-rate splits (L5, L10, home, away, vs-rank).
- **Defense detail:** game-by-game of what *this week's opponent* has allowed to this
  player's position this season. This is a headline feature, not a nice-to-have.
- The cached weekly AI read.
- Secondary/optional: the internal projected range (small, not the main claim).

**Weekly targets section:** for each position, the defenses giving up the most to it
this week — the "who to target" list. Already present in the prototype as the bottom
panel; can be surfaced more prominently.

**Charts:** Recharts throughout. Weather-adjusted projections where relevant.

**Book-only / late-line behavior:** college books post props late (often Thu/Fri for
Saturday games). Run the model on **all** projected starters and high-usage skill
players regardless of whether a line exists yet, so the tool is useful early in the
week. Attach book lines and compute the over/under call as books post them. A player
without a line yet still shows the model's lean; the confidence-vs-line call fills in
when the market appears.

**Design language — match Legends Sports (lgdsanalytics.vercel.app).** This ships
inside the client's existing site next to their MLB, tennis and WNBA engines, so it
must look native, not like a bolted-on tool. Their existing **MLB pitcher-props
model is the direct visual and interaction template** — build the football prop
cards to mirror it, then improve where it helps. Pull exact hex/font values from the
live site's CSS (DevTools) rather than trusting these approximations; wire everything
through CSS variables / Tailwind theme tokens so values can be corrected in one place.

Aesthetic: dark "neon-on-navy" sports-analytics look. Data-dense cards, lots of tiny
uppercase tracked labels over bold values, gradient accents on the numbers that matter.

- **Background:** deep near-black navy (~`#070B14`). **Panels:** slightly lifted navy
  (~`#0E1420`) with faint 1px borders (~`rgba(255,255,255,0.06)`); **inset sub-cards**
  darker than their parent.
- **Signature gradient:** cyan → indigo/purple, used on the wordmark, the key % values,
  and primary CTAs. Cyan ~`#22D3EE`, indigo ~`#6366F1`.
- **Semantic colors:** success/hit green ~`#22C55E`; under/loss red-pink ~`#F43F5E`;
  target/total amber ~`#F5A623`. Text near-white `#F1F5F9`, muted blue-gray `#64748B`,
  dim label gray for the tiny uppercase captions.
- **Type:** modern geometric sans. Section headers are UPPERCASE with wide tracking
  (~0.12em), bold. Tiny uppercase labels (SELECTION, ODDS (BOOK), CONF) sit above
  bold values. Big numbers are heavy weight; hero % values take the cyan→indigo
  gradient as text fill. (Confirm the actual font family from the site.)
- **Components to reuse from their pitcher model:** rounded cards (~14px) with a
  header row (player name + a position pill like RHP/LHP → here QB/RB/WR/TE, plus a
  GRADE and CONF badge); per-market sub-cards each showing the market label with an
  emoji marker, an OVER/UNDER pill carrying the edge %, a **projected-vs-line
  horizontal bar** with the line marked, and the book name with two-way odds; a
  **last-5 row** of small colored circles (their "LAST 5 (80%) HITS UNDER: ● ● ● ● ●");
  an **EDGES ONLY** toggle; a **sort-priority** switch (ALL / HIT RATE (L5) / rank);
  pill-group filters and an icon search field. A **date selector** strip
  (2 days ago → 2 days away) sits above the board.
- Rounded, fully-pill status badges (LIVE, BETA), gradient CTAs in uppercase with a
  trailing arrow, and emoji as section markers (⚡ 🔥 🎯) are all part of the house style.

Note on our over/under-first output: their pitcher model shows the raw projection
next to the line (e.g. "Proj 1.5 · Line 4.5"), which can look wrong in individual
cases even when the probability is sound. Our models lead with the OVER/UNDER call
and confidence % instead, keeping the projected range as secondary detail. This is
the client's stated direction for the new models; honor it while keeping the rest of
the card language identical to theirs.

**Team logos:** color-coded placeholder chips (team abbreviation on team colors), as
in the prototype. Real marks are trademarked and get added later at the client's
direction. Do not scrape or embed official logo assets.

---

## 8. Build sequence (phases — confirm plan at each, stop for review after each)

**Phase 1 — Foundations.** Repo scaffolding (Next.js + TS, Tailwind, Supabase client,
Python worker skeleton for Render). Supabase schema and migrations for players, teams,
games, player-game stats, play-by-play-derived splits, weekly rating snapshots, lines,
projections/results, and cached AI reads. Env/secrets wiring. *Deliverable: schema I
can inspect + a deploy that boots.*

**Phase 2 — Ingest.** CFBD adapter: backfill recent seasons for all FBS, plus an
in-week refresh job. Build the position-split engine and opponent adjustment. Store
rating snapshots point-in-time. *Deliverable: populated tables, row counts, a spot-
check that splits look sane.*

**Phase 3 — Model + backtest.** Per-market distribution models, the anytime-TD
probability model, the college weighting rules. The backtest and calibration report.
*Deliverable: the calibration report. No UI yet. We review before Phase 4.*

**Phase 4 — Application.** The board, controls, filters, player detail, defense
detail, weekly targets, charts. Reads from Supabase only. Themeable tokens.
*Deliverable: the working dashboard on live data.*

**Phase 5 — Odds + AI + polish.** Odds ingestion and the over/under call where markets
exist (see configurable items below), weekly cached AI reads, reskin to the client's
theme, monitoring/alerting on the pipeline, docs. *Deliverable: production-ready.*

---

## 9. Configurable / to-confirm (do not hardcode)

Two decisions are still open with the client. Build both as configuration so they
don't block progress and can be set later:

1. **Odds source / coverage.** Lines come from the client's Odds API key; it's unconfirmed
   whether the plan covers NCAAF player props. Make the odds ingestion a pluggable
   adapter so the source can change and so the app degrades gracefully (model leans
   show even when no line is attached).
2. **Hit-rate basis.** Either measured against the historical **closing line** (needs a
   paid line backfill) or against a **fixed threshold applied to past games** (cheaper,
   and what most props sites do). Make this a config flag; support the threshold method
   as the default and leave a clean path to the closing-line method.

---

## 10. Out of scope / explicitly not now

- Full **game-outcome / spread prediction** — different, harder, separate project.
- Real team logo/wordmark assets — placeholders only until licensed.
- Any per-page-view LLM calls — analysis is weekly and cached.
- Betting, bankroll, or automated wagering features — this is an analysis tool.
