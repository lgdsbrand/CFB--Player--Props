import {
  atsResult,
  formatPoints,
  formatRecord,
  teamRecord,
  totalResult,
  type GradedGame,
} from "@/lib/core/game-lines";

/**
 * Against the spread and the total, both teams this season, plus the
 * head-to-head (CLAUDE.md §11, G1) — the Covers screens the client sent.
 *
 * Graded against CFBD's consensus line: the median of ESPN Bet, DraftKings and
 * Bovada, refreshed daily until kickoff. The caption says so, because a record
 * graded against a different number is a different record.
 */

interface Side {
  teamId: number;
  label: string;
}

export function RecordsPanel({
  season,
  away,
  home,
  seasonGames,
  meetings,
  earliestSeason,
}: {
  season: number;
  away: Side;
  home: Side;
  seasonGames: GradedGame[];
  meetings: GradedGame[];
  earliestSeason: number | null;
}) {
  return (
    <section className="panel flex flex-col gap-4 p-4">
      <div className="flex flex-col gap-1">
        <h2 className="section-header">🔥 Against the number</h2>
        <p className="text-muted text-xs">
          {season} record straight up, against the spread and on the total, in
          games before this one. Graded against CollegeFootballData&rsquo;s
          consensus line (median of ESPN Bet, DraftKings, Bovada) — the last
          number it recorded, which is usually but not always the close.
        </p>
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        {[away, home].map((side) => {
          const record = teamRecord(seasonGames, side.teamId);
          const played = record.straightUp.w + record.straightUp.l;
          return (
            <div key={side.teamId} className="bg-panel-inset flex flex-col gap-2 rounded-xl p-3">
              <span className="text-ink text-sm font-extrabold">{side.label}</span>
              {played === 0 ? (
                <span className="text-dim text-xs">No completed games yet this season.</span>
              ) : (
                <div className="flex flex-wrap gap-x-5 gap-y-1">
                  <Stat label="SU" value={formatRecord(record.straightUp.w, record.straightUp.l)} />
                  <Stat
                    label="ATS"
                    value={formatRecord(record.ats.w, record.ats.l, record.ats.p)}
                  />
                  <Stat
                    label="O / U"
                    value={formatRecord(record.totals.o, record.totals.u, record.totals.p)}
                  />
                  {record.noLine > 0 ? (
                    <span className="text-dim self-end text-[0.6875rem]">
                      {record.noLine} without a line
                    </span>
                  ) : null}
                </div>
              )}
            </div>
          );
        })}
      </div>

      <div className="flex flex-col gap-2">
        <h3 className="label-caption">Head to head</h3>
        {meetings.length === 0 ? (
          <p className="text-dim text-xs">
            No meeting on record
            {earliestSeason !== null ? ` since ${earliestSeason}` : ""} — our game
            history starts there.
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[30rem] border-collapse text-xs">
              <thead>
                <tr className="border-border-subtle border-b">
                  <th scope="col" className="label-caption py-1.5 pr-3 text-left">Season</th>
                  <th scope="col" className="label-caption py-1.5 pr-3 text-left">Score</th>
                  <th scope="col" className="label-caption py-1.5 pr-3 text-left">Line</th>
                  <th scope="col" className="label-caption py-1.5 pr-3 text-left">
                    {home.label} ATS
                  </th>
                  <th scope="col" className="label-caption py-1.5 text-left">Total</th>
                </tr>
              </thead>
              <tbody>
                {meetings.map((game) => {
                  const homeWasHome = game.homeTeamId === home.teamId;
                  const homePts = homeWasHome ? game.homePoints : game.awayPoints;
                  const awayPts = homeWasHome ? game.awayPoints : game.homePoints;
                  const ats = atsResult(game, home.teamId);
                  const total = totalResult(game);
                  // The line from TODAY's home team's side, whichever side it
                  // was at home in that meeting.
                  const line =
                    game.homeSpread === null
                      ? null
                      : homeWasHome
                        ? game.homeSpread
                        : -game.homeSpread;
                  return (
                    <tr key={game.gameId} className="border-border-subtle/60 border-b last:border-0">
                      <td className="text-muted py-1.5 pr-3 tabular-nums">
                        {game.season} · Wk {game.week}
                      </td>
                      <td className="text-ink py-1.5 pr-3 font-semibold tabular-nums">
                        {away.label} {awayPts} – {homePts} {home.label}
                      </td>
                      <td className="text-muted py-1.5 pr-3 tabular-nums">
                        {line === null
                          ? "—"
                          : `${home.label} ${line === 0 ? "PK" : `${line > 0 ? "+" : "-"}${formatPoints(line)}`}`}
                      </td>
                      <td className="py-1.5 pr-3 font-bold">
                        <Result value={ats} />
                      </td>
                      <td className="py-1.5">
                        {total === null ? (
                          <span className="text-dim">—</span>
                        ) : (
                          <span className="text-muted">
                            {total === "O" ? "Over" : total === "U" ? "Under" : "Push"}{" "}
                            {game.total !== null ? formatPoints(game.total) : ""}
                          </span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        {earliestSeason !== null && meetings.length > 0 ? (
          <p className="text-dim text-[0.6875rem]">Meetings since {earliestSeason}.</p>
        ) : null}
      </div>
    </section>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <span className="flex flex-col">
      <span className="label-caption">{label}</span>
      <span className="text-ink text-lg font-extrabold tabular-nums">{value}</span>
    </span>
  );
}

function Result({ value }: { value: "W" | "L" | "P" | null }) {
  if (value === null) return <span className="text-dim">—</span>;
  const cls = value === "W" ? "text-positive" : value === "L" ? "text-negative" : "text-muted";
  return <span className={cls}>{value === "W" ? "Cover" : value === "L" ? "No cover" : "Push"}</span>;
}
