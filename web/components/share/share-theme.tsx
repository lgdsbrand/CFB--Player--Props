/**
 * The share images' shared look, for `next/og` (Satori): sizes, the theme's
 * colours and the small building blocks every graphic uses.
 *
 * SATORI IS NOT A BROWSER. Flexbox only, every element with more than one
 * child needs `display: flex`, there are no CSS variables (so the theme's
 * values are written out: the hex equivalents app/globals.css records beside
 * each token; change both together), and a style key set to `undefined`
 * throws ("reading 'trim'").
 */

export const SHARE_WIDTH = 1080;
export const SHARE_HEIGHT = 1350;

export const C = {
  canvas: "#0f172a",
  panel: "#1d293d",
  inset: "#020618",
  border: "#314158",
  cyan: "#00d3f2",
  indigo: "#615fff",
  positive: "#00c950",
  negative: "#ff2056",
  target: "#fe9a00",
  ink: "#ffffff",
  muted: "#90a1b9",
  dim: "#62748e",
};

export const GRADIENT = `linear-gradient(90deg, ${C.cyan}, ${C.indigo})`;

export function Panel({ children, accent = false }: { children: React.ReactNode; accent?: boolean }) {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 14,
        padding: "22px 28px",
        borderRadius: 22,
        backgroundColor: C.panel,
        border: `2px solid ${accent ? "rgba(254,154,0,0.55)" : C.border}`,
      }}
    >
      {children}
    </div>
  );
}

export function Cell({ children, width, grow = false }: { children: React.ReactNode; width?: number; grow?: boolean }) {
  // A style key set to `undefined` crashes Satori ("reading 'trim'"), so the
  // width is spread in only when there is one.
  return (
    <div style={{ display: "flex", flexGrow: grow ? 1 : 0, flexShrink: 0, ...(width ? { width } : {}) }}>
      {children}
    </div>
  );
}

export function Caption({ children }: { children: React.ReactNode }) {
  return (
    <span style={{ fontSize: 18, fontWeight: 800, letterSpacing: "0.14em", color: C.dim, textTransform: "uppercase" }}>
      {children}
    </span>
  );
}

export function Pill({ children, color }: { children: React.ReactNode; color: string }) {
  return (
    <span
      style={{
        display: "flex",
        padding: "4px 14px",
        borderRadius: 999,
        fontSize: 22,
        fontWeight: 800,
        color,
        border: `2px solid ${color}`,
      }}
    >
      {children}
    </span>
  );
}
