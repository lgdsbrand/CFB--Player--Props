import { readableOn } from "@/components/board/team-chip";
import type { ShareGameView, ShareLine, ShareTeam } from "@/lib/core/share-game";

import { C, Caption, Cell, GRADIENT, Panel, Pill, SHARE_HEIGHT, SHARE_WIDTH } from "./share-theme";

export { SHARE_HEIGHT, SHARE_WIDTH };

/**
 * The game share graphic's layout, for `next/og` (Satori), 1080 x 1350: the
 * portrait size Instagram and X both show uncropped. Colours and building
 * blocks, and Satori's rules, are in `./share-theme`.
 */
export function GameShareImage({ view, site }: { view: ShareGameView; site: string }) {
  const scoreLabel = view.completed ? "Final" : "Projected score";
  return (
    <div
      style={{
        width: SHARE_WIDTH,
        height: SHARE_HEIGHT,
        display: "flex",
        flexDirection: "column",
        padding: "52px 60px 40px",
        gap: 22,
        backgroundColor: C.canvas,
        backgroundImage: `radial-gradient(circle at 85% 0%, rgba(97,95,255,0.22), transparent 55%), radial-gradient(circle at 0% 100%, rgba(0,211,242,0.12), transparent 50%)`,
        color: C.ink,
        fontFamily: "Inter",
      }}
    >
      {/* Brand and when */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <span
            style={{
              fontSize: 34,
              fontWeight: 800,
              letterSpacing: "0.06em",
              backgroundImage: GRADIENT,
              backgroundClip: "text",
              color: "transparent",
            }}
          >
            LEGENDS SPORTS
          </span>
          <span style={{ fontSize: 20, fontWeight: 700, letterSpacing: "0.14em", color: C.muted }}>
            {`${view.sportLabel} · ${view.weekLabel.toUpperCase()}${view.neutralSite ? " · NEUTRAL SITE" : ""}`}
          </span>
        </div>
        <span style={{ fontSize: 24, fontWeight: 700, color: C.muted }}>{view.kickoff}</span>
      </div>

      {/* The panels sit centred between the header and the footer, so a game
          with no rule plays or no prices is not top-heavy. */}
      <div style={{ display: "flex", flexDirection: "column", gap: 22, flexGrow: 1, justifyContent: "center" }}>
      {/* Matchup */}
      <Panel>
        <div style={{ display: "flex", justifyContent: "flex-end" }}>
          <Caption>{scoreLabel}</Caption>
        </div>
        <TeamRow team={view.away} completed={view.completed} />
        <div style={{ height: 1, backgroundColor: C.border, display: "flex" }} />
        <TeamRow team={view.home} completed={view.completed} home />
        {view.winChance ? (
          <span style={{ fontSize: 24, color: C.muted, fontWeight: 700 }}>{`Win chance ${view.winChance}`}</span>
        ) : null}
      </Panel>

      {/* Our number against the book's */}
      <Panel>
        <div style={{ display: "flex", gap: 16 }}>
          <Cell width={140}><Caption>Market</Caption></Cell>
          <Cell width={210}><Caption>Book</Caption></Cell>
          <Cell width={200}><Caption>Model</Caption></Cell>
          <Cell grow><Caption>Lean</Caption></Cell>
        </div>
        {view.lines.map((line) => (
          <LineRow key={line.label} line={line} />
        ))}
        {view.lines.some((l) => l.bookName) ? (
          <span style={{ fontSize: 18, color: C.dim }}>
            {`Book line: ${view.lines.find((l) => l.bookName)?.bookName}. Edge = model chance minus the book's, vig removed.`}
          </span>
        ) : null}
      </Panel>

      {view.plays.length > 0 ? (
        <Panel accent>
          <Caption>🎯 Rule plays</Caption>
          {view.plays.map((play) => (
            <div key={play.label} style={{ display: "flex", alignItems: "center", gap: 18 }}>
              <span style={{ fontSize: 32, fontWeight: 800 }}>{play.label}</span>
              {play.topTier ? <Pill color={C.target}>🔥 7+</Pill> : null}
              {play.result ? (
                <Pill color={play.result === "Won" ? C.positive : play.result === "Lost" ? C.negative : C.muted}>
                  {play.result}
                </Pill>
              ) : null}
              <span style={{ fontSize: 21, color: C.muted, marginLeft: "auto" }}>{play.reason}</span>
            </div>
          ))}
        </Panel>
      ) : null}

      {view.books.length > 0 ? (
        <Panel>
          <div style={{ display: "flex", gap: 14 }}>
            <Cell width={150}><Caption>📊 Book</Caption></Cell>
            <Cell width={210}><Caption>Spread</Caption></Cell>
            <Cell width={230}><Caption>Total</Caption></Cell>
            <Cell grow><Caption>Moneyline</Caption></Cell>
          </div>
          {view.books.map((book) => (
            <div key={book.name} style={{ display: "flex", alignItems: "baseline", gap: 14 }}>
              <Cell width={150}><span style={{ fontSize: 21, fontWeight: 800, color: C.muted }}>{book.name}</span></Cell>
              <Cell width={210}><span style={{ fontSize: 21, fontWeight: 700 }}>{book.spread ?? "—"}</span></Cell>
              <Cell width={230}><span style={{ fontSize: 21, fontWeight: 700 }}>{book.total ?? "—"}</span></Cell>
              <Cell grow><span style={{ fontSize: 21, fontWeight: 700 }}>{book.moneyline ?? "—"}</span></Cell>
            </div>
          ))}
        </Panel>
      ) : null}

      </div>

      {/* Footer pinned to the bottom */}
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", fontSize: 22 }}>
          <span style={{ fontWeight: 700, color: C.muted }}>{view.weather ?? " "}</span>
          <span style={{ fontWeight: 800, color: C.cyan }}>{site}</span>
        </div>
        <span style={{ fontSize: 16, color: C.dim }}>
          Model analysis, not betting advice. 21+. Prices as of when this was shared.
        </span>
      </div>
    </div>
  );
}

function TeamRow({ team, completed, home = false }: { team: ShareTeam; completed: boolean; home?: boolean }) {
  const big = completed ? team.final : team.projected;
  const chipText = team.color ? readableOn(team.color, team.altColor) : C.muted;
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 22 }}>
      <span
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          minWidth: 120,
          height: 64,
          padding: "0 14px",
          borderRadius: 12,
          fontSize: 30,
          fontWeight: 800,
          backgroundColor: team.color ?? C.inset,
          color: chipText,
        }}
      >
        {team.abbreviation}
      </span>
      <div style={{ display: "flex", flexDirection: "column", flexGrow: 1 }}>
        <span style={{ fontSize: 44, fontWeight: 800, lineHeight: 1.1 }}>
          {team.rank !== null ? `#${team.rank} ${team.school}` : team.school}
        </span>
        {home ? <Caption>Home</Caption> : null}
      </div>
      <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end" }}>
        {big !== null ? (
          <span
            style={{
              fontSize: 72,
              fontWeight: 800,
              lineHeight: 1,
              backgroundImage: GRADIENT,
              backgroundClip: "text",
              color: "transparent",
            }}
          >
            {String(big)}
          </span>
        ) : null}
        {completed && team.projected ? (
          <span style={{ fontSize: 20, color: C.dim }}>{`model ${team.projected}`}</span>
        ) : null}
      </div>
    </div>
  );
}

function LineRow({ line }: { line: ShareLine }) {
  const pass = line.lean === "Pass";
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
      <Cell width={140}>
        <span style={{ fontSize: 22, fontWeight: 800, color: C.muted, letterSpacing: "0.1em" }}>
          {line.label.toUpperCase()}
        </span>
      </Cell>
      <Cell width={210}>
        <span style={{ fontSize: 30, fontWeight: 800 }}>{line.book ?? "—"}</span>
      </Cell>
      <Cell width={200}>
        <span style={{ fontSize: 30, fontWeight: 800, color: line.ours ? C.cyan : C.dim }}>{line.ours ?? "—"}</span>
      </Cell>
      <Cell grow>
        {line.lean ? (
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <span style={{ fontSize: 30, fontWeight: 800, color: pass ? C.dim : C.ink }}>{line.lean}</span>
            {line.edge ? <Pill color={C.target}>{line.edge}</Pill> : null}
          </div>
        ) : (
          <span style={{ fontSize: 30, color: C.dim }}>—</span>
        )}
      </Cell>
    </div>
  );
}
