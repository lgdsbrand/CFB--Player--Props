import { formatAmericanOdds } from "@/lib/core/format";
import {
  formatPoints,
  type BookOdds,
  type MarketRole,
} from "@/lib/core/game-lines";

/**
 * Every captured book's price on one game, sharp and exchanges first
 * (CLAUDE.md §11, G1) — the side-by-side comparison the client asked for.
 *
 * Spreads print from each TEAM's side ("-7.0 -110 / +7.0 -110"), because the
 * stored home-perspective number is a storage convention, not something a
 * reader should have to flip in their head.
 */

const ROLE_ORDER: MarketRole[] = ["sharp", "exchange", "retail", "other"];
const ROLE_LABEL: Record<MarketRole, string> = {
  sharp: "Sharp",
  exchange: "Exchange",
  retail: "US retail",
  other: "Other",
};

interface BookRow {
  key: string;
  name: string;
  role: MarketRole;
  spread?: BookOdds;
  total?: BookOdds;
  moneyline?: BookOdds;
  capturedAt: string;
}

function rowsByBook(odds: BookOdds[]): BookRow[] {
  const byKey = new Map<string, BookRow>();
  for (const entry of odds) {
    const row =
      byKey.get(entry.sportsbookKey) ??
      ({
        key: entry.sportsbookKey,
        name: entry.sportsbookName,
        role: entry.role,
        capturedAt: entry.capturedAt,
      } as BookRow);
    if (entry.market === "spreads") row.spread = entry;
    else if (entry.market === "totals") row.total = entry;
    else row.moneyline = entry;
    if (entry.capturedAt > row.capturedAt) row.capturedAt = entry.capturedAt;
    byKey.set(entry.sportsbookKey, row);
  }
  return [...byKey.values()].sort(
    (a, b) =>
      ROLE_ORDER.indexOf(a.role) - ROLE_ORDER.indexOf(b.role) ||
      a.name.localeCompare(b.name),
  );
}

function signed(points: number): string {
  if (points === 0) return "PK";
  return `${points > 0 ? "+" : "-"}${formatPoints(points)}`;
}

export function BookOddsPanel({
  odds,
  home,
  away,
}: {
  odds: BookOdds[];
  home: string;
  away: string;
}) {
  const rows = rowsByBook(odds);

  return (
    <section className="panel flex flex-col gap-3 p-4">
      <div className="flex flex-col gap-1">
        <h2 className="section-header">📊 Lines by book</h2>
        <p className="text-muted text-xs">
          Current price at every book we capture, sharp first. &ldquo;Moved&rdquo;
          is the change since the first price we saw this week — our first
          capture, not the book&rsquo;s opening line.
        </p>
      </div>

      {rows.length === 0 ? (
        <p className="text-muted text-xs">
          No book has priced this game in our captures yet.
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[46rem] border-collapse text-sm">
            <thead>
              <tr className="border-border-subtle border-b">
                <Th>Book</Th>
                <Th>{away} spread</Th>
                <Th>{home} spread</Th>
                <Th>Moved</Th>
                <Th>Total O / U</Th>
                <Th>{away} ML</Th>
                <Th>{home} ML</Th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row, index) => {
                const firstOfRole = index === 0 || rows[index - 1].role !== row.role;
                const spreadLine = row.spread?.line ?? null;
                const moved =
                  spreadLine !== null && row.spread?.firstLine !== null && row.spread?.firstLine !== undefined
                    ? Math.round((spreadLine - row.spread.firstLine) * 100) / 100
                    : null;
                return (
                  <tr
                    key={row.key}
                    className={
                      "border-border-subtle/60 border-b last:border-0 " +
                      (firstOfRole && index > 0 ? "border-t-border-strong border-t" : "")
                    }
                  >
                    <th scope="row" className="py-2 pr-3 text-left">
                      <span className="flex flex-col">
                        <span className="text-ink text-xs font-bold">{row.name}</span>
                        {firstOfRole ? (
                          <span
                            className={
                              "label-caption " +
                              (row.role === "sharp" ? "text-accent-cyan" : "")
                            }
                          >
                            {ROLE_LABEL[row.role]}
                          </span>
                        ) : null}
                      </span>
                    </th>
                    <Td>
                      {spreadLine === null
                        ? null
                        : `${signed(-spreadLine)} ${formatAmericanOdds(row.spread?.awayPrice ?? null)}`}
                    </Td>
                    <Td>
                      {spreadLine === null
                        ? null
                        : `${signed(spreadLine)} ${formatAmericanOdds(row.spread?.homePrice ?? null)}`}
                    </Td>
                    <Td dim>
                      {moved === null || moved === 0
                        ? null
                        : `${home} ${moved > 0 ? "+" : "-"}${formatPoints(moved)}`}
                    </Td>
                    <Td>
                      {row.total?.line === null || row.total?.line === undefined
                        ? null
                        : `${formatPoints(row.total.line)}  ${formatAmericanOdds(row.total.overPrice)} / ${formatAmericanOdds(row.total.underPrice)}`}
                    </Td>
                    <Td>{row.moneyline ? formatAmericanOdds(row.moneyline.awayPrice) : null}</Td>
                    <Td>{row.moneyline ? formatAmericanOdds(row.moneyline.homePrice) : null}</Td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function Th({ children }: { children: React.ReactNode }) {
  return (
    <th scope="col" className="label-caption py-2 pr-3 text-left whitespace-nowrap">
      {children}
    </th>
  );
}

function Td({ children, dim }: { children: React.ReactNode; dim?: boolean }) {
  return (
    <td
      className={
        "py-2 pr-3 text-xs whitespace-nowrap tabular-nums " +
        (dim ? "text-muted" : "text-ink font-semibold")
      }
    >
      {children ?? <span className="text-dim">—</span>}
    </td>
  );
}
