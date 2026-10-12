import { formatAmericanOdds, formatEdge } from "@/lib/core/format";
import { VERDICT_LABEL, type SlipCheck, type Verdict } from "@/lib/core/slip-check";
import { legMatchup, legTitle } from "@/lib/core/slip";

import { C, Caption, GRADIENT, Panel, Pill, SHARE_HEIGHT, SHARE_WIDTH } from "./share-theme";

/**
 * A checked slip as a graphic (client, 2026-10-12: his reference's "Download
 * PNG"), 1080 x 1350. The numbers are the checker's, recomputed on the server
 * from the leg keys in the URL (`app/slip-check/image`).
 */

const pct = (p: number | null) => (p === null ? "—" : `${(p * 100).toFixed(1)}%`);
const price = (p: number | null) => (p === null ? "—" : formatAmericanOdds(p));

const VERDICT_COLOR: Record<Verdict, string> = {
  agrees: C.positive,
  disagrees: C.negative,
  close: C.muted,
  "no-model": C.dim,
  "not-priced": C.target,
  "not-found": C.dim,
};

export function SlipCheckImage({
  check,
  bookName,
  heading,
  site,
}: {
  check: SlipCheck;
  bookName: string;
  /** "NCAAF · WEEK 7". */
  heading: string;
  site: string;
}) {
  const single = check.legs.length === 1;
  // Twelve legs must fit: rows shrink as the slip grows.
  const dense = check.legs.length > 6;
  const note =
    check.blocked === "same-game"
      ? "Two legs share a game, so no combined chance: they are correlated."
      : check.blocked === "no-model"
        ? "A leg has no model number, so no combined chance."
        : null;

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
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <span
            style={{ fontSize: 34, fontWeight: 800, letterSpacing: "0.06em", backgroundImage: GRADIENT, backgroundClip: "text", color: "transparent" }}
          >
            LEGENDS SPORTS
          </span>
          <span style={{ fontSize: 20, fontWeight: 700, letterSpacing: "0.14em", color: C.muted }}>
            {`SLIP CHECK · ${heading}`}
          </span>
        </div>
        <span style={{ fontSize: 26, fontWeight: 800, color: C.muted }}>{bookName}</span>
      </div>

      {/* Centred between header and footer, so a short slip is not top-heavy. */}
      <div style={{ display: "flex", flexDirection: "column", gap: 22, flexGrow: 1, justifyContent: "center" }}>
        <Panel>
          <div style={{ display: "flex", gap: 16 }}>
            <Tile label={single ? "Model chance" : "Model: all hit"} value={pct(check.modelProb)} accent />
            <Tile label="Book chance, no vig" value={pct(check.bookProb)} />
          </div>
          <div style={{ display: "flex", gap: 16 }}>
            <Tile label="Fair price" value={price(check.fairPrice)} />
            <Tile label={`${bookName} price`} value={single ? price(check.legs[0]?.price ?? null) : price(check.bookPrice)} />
          </div>
          {check.value !== null ? (
            <span style={{ fontSize: 22, color: C.muted }}>
              {`Value at ${bookName}'s price: ${formatEdge(check.value)} per unit, if the model is right.`}
            </span>
          ) : null}
          {note ? <span style={{ fontSize: 22, color: C.target }}>{note}</span> : null}
        </Panel>

        <div style={{ display: "flex", flexDirection: "column", gap: dense ? 8 : 12 }}>
          <Caption>{`${check.legs.length} leg${single ? "" : "s"}`}</Caption>
          {check.legs.map((leg) => (
            <div
              key={leg.key}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 18,
                padding: dense ? "10px 22px" : "16px 24px",
                borderRadius: 16,
                backgroundColor: C.panel,
                border: `2px solid ${C.border}`,
              }}
            >
              <div style={{ display: "flex", flexDirection: "column", flexGrow: 1, flexShrink: 1, minWidth: 0 }}>
                <span style={{ fontSize: dense ? 22 : 27, fontWeight: 800 }}>{legTitle(leg.leg)}</span>
                <span style={{ fontSize: dense ? 16 : 19, color: C.muted }}>
                  {`${legMatchup(leg.leg)} · Model ${pct(leg.modelProb)} · Book ${pct(leg.bookProb)} · ${price(leg.price)}`}
                </span>
              </div>
              <Pill color={VERDICT_COLOR[leg.verdict]}>{VERDICT_LABEL[leg.verdict]}</Pill>
            </div>
          ))}
      </div>

      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        <div style={{ display: "flex", justifyContent: "flex-end", fontSize: 22 }}>
          <span style={{ fontWeight: 800, color: C.cyan }}>{site}</span>
        </div>
        <span style={{ fontSize: 16, color: C.dim }}>
          Model analysis, not betting advice. 21+. Legs combined as independent. Prices as of when this was shared.
        </span>
      </div>
    </div>
  );
}

function Tile({ label, value, accent = false }: { label: string; value: string; accent?: boolean }) {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 4,
        flexGrow: 1,
        flexBasis: 0,
        padding: "16px 22px",
        borderRadius: 16,
        backgroundColor: C.inset,
      }}
    >
      <Caption>{label}</Caption>
      <span
        style={
          accent
            ? { fontSize: 52, fontWeight: 800, backgroundImage: GRADIENT, backgroundClip: "text", color: "transparent" }
            : { fontSize: 52, fontWeight: 800 }
        }
      >
        {value}
      </span>
    </div>
  );
}
