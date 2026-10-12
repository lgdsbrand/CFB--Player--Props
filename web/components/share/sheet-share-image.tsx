import { readableOn } from "@/components/board/team-chip";
import type { ShareSheetView } from "@/lib/core/share-sheet";

import { C, GRADIENT, SHARE_HEIGHT, SHARE_WIDTH } from "./share-theme";

/**
 * The cheat sheet as a graphic (client, 2026-10-12), 1080 x 1350, after his
 * Linemate reference: one row per streak, the record on the right. Team marks
 * are abbreviation chips on team colours, never logos (CLAUDE.md §7, §10).
 */

const STANCE: Record<string, { text: string; color: string } | null> = {
  agrees: { text: "Model agrees", color: C.positive },
  disagrees: { text: "Model disagrees", color: C.negative },
  "no-call": null,
};

export function SheetShareImage({ view, site }: { view: ShareSheetView; site: string }) {
  return (
    <div
      style={{
        width: SHARE_WIDTH,
        height: SHARE_HEIGHT,
        display: "flex",
        flexDirection: "column",
        padding: "52px 60px 40px",
        gap: 18,
        backgroundColor: C.canvas,
        backgroundImage: `radial-gradient(circle at 85% 0%, rgba(97,95,255,0.22), transparent 55%), radial-gradient(circle at 0% 100%, rgba(0,211,242,0.12), transparent 50%)`,
        color: C.ink,
        fontFamily: "Inter",
      }}
    >
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        <span
          style={{ fontSize: 30, fontWeight: 800, letterSpacing: "0.06em", backgroundImage: GRADIENT, backgroundClip: "text", color: "transparent" }}
        >
          LEGENDS SPORTS
        </span>
        <span style={{ fontSize: 64, fontWeight: 800, lineHeight: 1.05 }}>{view.title}</span>
        <span style={{ fontSize: 26, fontWeight: 700, color: C.muted }}>{view.subtitle}</span>
      </div>

      <div style={{ display: "flex", padding: "6px 22px 0", justifyContent: "space-between" }}>
        <span style={{ fontSize: 18, fontWeight: 800, letterSpacing: "0.14em", color: C.dim }}>PLAYER</span>
        <span style={{ fontSize: 18, fontWeight: 800, letterSpacing: "0.14em", color: C.dim }}>HIT RATE</span>
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 10, flexGrow: 1 }}>
        {view.rows.length === 0 ? (
          <span style={{ fontSize: 28, color: C.muted, padding: "0 22px" }}>Nothing on this sheet right now.</span>
        ) : null}
        {view.rows.map((row, i) => {
          const stance = STANCE[row.agreement];
          return (
            <div
              key={`${row.player}-${row.label}-${i}`}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 18,
                padding: "10px 22px",
                borderRadius: 16,
                backgroundColor: C.panel,
                border: `2px solid ${C.border}`,
              }}
            >
              <span
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  minWidth: 96,
                  height: 48,
                  padding: "0 10px",
                  borderRadius: 10,
                  fontSize: 22,
                  fontWeight: 800,
                  backgroundColor: row.color ?? C.inset,
                  color: row.color ? readableOn(row.color, row.altColor) : C.muted,
                }}
              >
                {row.abbreviation}
              </span>
              <div style={{ display: "flex", flexDirection: "column", flexGrow: 1, flexShrink: 1, minWidth: 0 }}>
                <span style={{ fontSize: 28, fontWeight: 800 }}>{`${row.player}: ${row.label}`}</span>
                {stance ? (
                  <span style={{ fontSize: 18, fontWeight: 700, color: stance.color }}>{stance.text}</span>
                ) : null}
              </div>
              <span style={{ fontSize: 32, fontWeight: 800, color: C.cyan }}>{row.record}</span>
            </div>
          );
        })}
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        <div style={{ display: "flex", justifyContent: "space-between", fontSize: 22 }}>
          <span style={{ fontWeight: 700, color: C.muted }}>
            {view.more > 0 ? `+ ${view.more} more on the cheat sheet` : " "}
          </span>
          <span style={{ fontWeight: 800, color: C.cyan }}>{site}</span>
        </div>
        <span style={{ fontSize: 16, color: C.dim }}>
          Past games against today&apos;s line. A streak is not an edge. Model analysis, not betting advice. 21+.
        </span>
      </div>
    </div>
  );
}
