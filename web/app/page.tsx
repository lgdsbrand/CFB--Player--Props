import Link from "next/link";
import { redirect } from "next/navigation";

import { NotConfigured } from "@/components/not-configured";
import { SiteHeader } from "@/components/site-header";
import {
  BOARD_PATH,
  boardParamsPresent,
  type RawParams,
} from "@/lib/core/board-params";
import { offenseOnBoard } from "@/lib/core/board-scope";
import { isSupabaseConfigured } from "@/lib/core/env";
import { formatCount, formatDateRange } from "@/lib/core/format";
import { homeTiles, pricingNote, type HomeTile } from "@/lib/core/home-view";
import { getAppConfig } from "@/lib/data/config";
import { kickoffCutoff, upcomingGames } from "@/lib/core/kickoff";
import { getSlateGames } from "@/lib/data/games";
import { getHomeCounts } from "@/lib/data/home";
import { findWeek, getSlateWeeks } from "@/lib/data/slate";
import { getTeamDirectory } from "@/lib/data/teams";

/**
 * The landing page.
 *
 * The client asked for a home page in front of the tools, matching how the rest
 * of his site presents its models. Four tiles, each carrying a live number for
 * the current slate — a landing page of four buttons with nothing behind them
 * would be worse than no landing page, because it adds a click and says
 * nothing.
 *
 * TWO OF THE FOUR ARE PRESET BOARD VIEWS, not new pages. "Best plays" is the
 * board sorted by confidence above a floor; "top edges" is the board on its own
 * edge filter. Building them as pages would duplicate the read layer, the
 * filters and the row-cap handling to ask questions the board already answers.
 * See `lib/core/home-view.ts` for the presets and the two honesty rules that
 * govern how they are labelled.
 */

export default async function Home({
  searchParams,
}: {
  searchParams: Promise<RawParams>;
}) {
  const raw = await searchParams;

  // The board lived here until this page took the address. Anything arriving
  // with board filters on it is a link shared before the move — forward it
  // rather than dropping the reader on a landing page and silently discarding
  // the week and filters they were pointed at.
  if (boardParamsPresent(raw)) {
    const query = new URLSearchParams();
    for (const [key, value] of Object.entries(raw)) {
      const single = Array.isArray(value) ? value[0] : value;
      if (single !== undefined) query.set(key, single);
    }
    redirect(`${BOARD_PATH}?${query.toString()}`);
  }

  if (!isSupabaseConfigured()) {
    return (
      <Shell>
        <NotConfigured />
      </Shell>
    );
  }

  const [weeks, config] = await Promise.all([getSlateWeeks(), getAppConfig()]);
  const active = findWeek(weeks, undefined, undefined);

  if (!active) {
    return (
      <Shell>
        <Hero subtitle="No week has model output yet." />
      </Shell>
    );
  }

  const cutoff = kickoffCutoff();
  // Counted over the games still to kick, matching every destination these
  // tiles link to.
  const games = upcomingGames(
    await getSlateGames(active.season, active.week),
    cutoff,
  );
  const teamDirectory = await getTeamDirectory(
    active.season,
    games.flatMap((game) => [game.homeTeamId, game.awayTeamId]),
  );

  // Counted the way the games index LISTS them, so the tile's number matches
  // what the reader finds when they click it.
  const listedGames = games.filter(
    (game) =>
      offenseOnBoard(teamDirectory.get(game.homeTeamId), undefined) ||
      offenseOnBoard(teamDirectory.get(game.awayTeamId), undefined),
  ).length;

  const counts = await getHomeCounts(
    active.season,
    active.week,
    config.edgeThreshold,
    listedGames,
    cutoff,
  );

  const tiles = homeTiles(counts, {
    season: active.season,
    week: active.week,
  });
  const note = pricingNote(counts);

  return (
    <Shell>
      <Hero
        subtitle={`${active.season} · Week ${active.week} · ${formatDateRange(
          active.firstKickoff,
          active.lastKickoff,
        )}`}
      />

      {/* An odd tile count leaves a hole in a two-column grid, so the last one
          takes the full width instead. A gap reads as something failing to
          render; a wide tile reads as a decision. */}
      <div className="grid gap-3 sm:grid-cols-2">
        {tiles.map((tile, index) => (
          <Tile
            key={tile.key}
            tile={tile}
            wide={tiles.length % 2 === 1 && index === tiles.length - 1}
          />
        ))}
      </div>

      {note ? (
        <p className="border-target/30 bg-target/5 text-muted rounded-xl border px-3 py-2 text-xs">
          <span className="text-target font-bold uppercase tracking-label">
            On the lines
          </span>{" "}
          — {note}
        </p>
      ) : null}

      {/*
        WHAT THE MODEL DOES, IN ONE PARAGRAPH, ON THE FRONT PAGE. The headline
        number everywhere in this product is a probability, not a projected stat
        line (CLAUDE.md §1), and that is unusual enough among props tools that a
        reader who is not told will assume the percentage means something else.
      */}
      <section className="panel flex flex-col gap-2 p-4">
        <h2 className="section-header">How to read it</h2>
        <p className="text-muted max-w-prose text-sm">
          For every player and market the model projects a full range of
          outcomes, not a single number. The <strong>over/under call</strong> is
          the side most of that range falls on, and the{" "}
          {/* Explicit, not a literal space. A space after a closing inline tag
              that OPENS a line is dropped by the JSX transform, which rendered
              this as "confidenceis" — the identical construction one line above
              keeps its space because the tag sits mid-line. */}
          <strong>confidence</strong>{" "}
          is how much of it clears the line.
          Touchdowns are stated the same way, as the probability of scoring at
          all. The projected range is on each player&rsquo;s page as supporting
          detail — it is the engine, not the claim.
        </p>
        <p className="text-dim max-w-prose text-sm">
          Edge is the model&rsquo;s probability minus the book&rsquo;s, after the
          vig is stripped out of the two-way price. It is a measure of
          disagreement with the market, not a promise about it.
        </p>
      </section>
    </Shell>
  );
}

function Hero({ subtitle }: { subtitle: string }) {
  return (
    <div className="flex flex-col gap-2">
      <span className="label-caption">Legends Sports · College Football</span>
      <h1 className="text-3xl font-extrabold tracking-tight sm:text-4xl">
        College Football{" "}
        <span className="gradient-text">Player Props</span>
      </h1>
      <p className="text-muted text-sm tabular-nums">{subtitle}</p>
    </div>
  );
}

/**
 * One tile, linked or explained.
 *
 * A TILE WITH NO ROWS IS NOT A LINK — see `home-view.ts` for why, and for the
 * three separate times this product has shipped a control that led nowhere. The
 * unlinked state is deliberately not styled as "disabled": it is a real panel
 * with a real number (zero) and a sentence about the market, because nothing is
 * broken and a greyed-out control implies something is.
 */
function Tile({ tile, wide = false }: { tile: HomeTile; wide?: boolean }) {
  const body = (
    <>
      <div className="flex items-baseline gap-2">
        <span aria-hidden className="text-lg leading-none">
          {tile.emoji}
        </span>
        <h2 className="section-header">{tile.title}</h2>
        {tile.href ? (
          <span className="text-muted group-hover:text-accent-cyan ml-auto text-xs font-bold transition-colors">
            →
          </span>
        ) : (
          <span className="label-caption text-dim ml-auto">Not yet</span>
        )}
      </div>

      <p className="flex items-baseline gap-2">
        <span
          className={
            "text-2xl font-extrabold leading-none tabular-nums " +
            (tile.count > 0 ? "gradient-text" : "text-dim")
          }
        >
          {formatCount(tile.count)}
        </span>
        <span className="label-caption">{tile.countLabel}</span>
      </p>

      <p className="text-muted text-sm">{tile.blurb}</p>

      {tile.unavailable ? (
        <p className="text-dim border-border-subtle border-t pt-2 text-xs">
          {tile.unavailable}
        </p>
      ) : tile.caveat ? (
        <p className="text-dim border-border-subtle border-t pt-2 text-xs">
          {tile.caveat}
        </p>
      ) : null}
    </>
  );

  const shell =
    "panel flex flex-col gap-3 p-5" + (wide ? " sm:col-span-2" : "");

  if (!tile.href) {
    return <div className={shell}>{body}</div>;
  }

  return (
    <Link
      href={tile.href}
      className={`${shell} hover:border-border-strong group transition-colors`}
    >
      {body}
    </Link>
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <>
      <SiteHeader activeHref="/" />
      <main className="mx-auto flex w-full max-w-5xl flex-col gap-5 px-4 py-8 sm:px-6">
        {children}
      </main>
    </>
  );
}
