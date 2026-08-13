import Link from "next/link";

import { formatDateShort } from "@/lib/core/format";
import type { PlayerGame } from "@/lib/core/player-games";
import { playerHref, type PlayerParams } from "@/lib/core/player-params";

/**
 * Which of this week's games the page is describing.
 *
 * RENDERS ONLY WHEN THERE ARE TWO, which is rare and worth stating plainly when
 * it happens. CFBD's week 1 spans nine or ten days, so a handful of teams play
 * twice inside one week number — 12 of them in 2026, putting 71 players here.
 * Every other week of the season this is absent entirely.
 *
 * It sits ABOVE the market tabs because it scopes them: the markets, the chart,
 * the defense panel and the conditions all belong to one game, and a reader who
 * does not notice this control would otherwise have no way to explain why the
 * opponent changed.
 */
export function GameTabs({
  games,
  activeGameId,
  params,
}: {
  games: PlayerGame[];
  activeGameId: number | null;
  params: PlayerParams;
}) {
  if (games.length < 2) return null;

  return (
    <nav aria-label="Games this week" className="flex flex-col gap-1.5">
      <span className="label-caption">
        Two games this week — showing one
      </span>
      <div className="flex flex-wrap gap-1.5">
        {games.map((game) => {
          const active = game.gameId === activeGameId;
          return (
            <Link
              key={game.gameId}
              href={playerHref(params, { game: game.gameId })}
              aria-current={active ? "page" : undefined}
              className={
                "flex items-baseline gap-2 rounded-xl border px-3 py-2 transition-colors " +
                (active
                  ? "border-accent-cyan/50 bg-accent-indigo/10"
                  : "border-border-subtle bg-panel hover:border-border-strong")
              }
            >
              {/* `isHome` alone, matching the page header two lines above.
                  A neutral site is strictly neither, but the header renders
                  "@" for it and notes the neutrality separately, and one page
                  disagreeing with itself about which team is at home is worse
                  than an imprecise preposition. */}
              <span
                className={
                  "text-[0.6875rem] font-bold uppercase tracking-label " +
                  (active ? "text-ink" : "text-muted")
                }
              >
                {game.isHome ? "vs" : "@"}{" "}
                {game.opponentAbbreviation ?? game.opponentSchool}
              </span>
              {/* THE DATE, NOT THE WEEKDAY. These two games can be nine days
                  apart — that is the entire reason this control exists — and
                  "Sat 4:00 PM" beside "Fri 9:30 PM" gives a reader no way to
                  tell which comes first. */}
              <span className="text-dim text-[0.625rem]">
                {formatDateShort(game.startDate)}
              </span>
            </Link>
          );
        })}
      </div>
    </nav>
  );
}
