/**
 * A team's abbreviation on its own colours.
 *
 * Placeholder marks by design: real logos and wordmarks are trademarked and get
 * added later at the client's direction (CLAUDE.md §7, §10). Nothing here
 * scrapes or embeds an official asset.
 *
 * The two colours come from `teams`, so this is the one place in the UI that
 * legitimately renders a value rather than a token — a team's colour is data,
 * not theme. A team with no colour on file falls back to panel styling rather
 * than to an invented one.
 */
export function TeamChip({
  abbreviation,
  color,
  altColor,
  title,
}: {
  abbreviation: string | null;
  color: string | null;
  altColor: string | null;
  title?: string;
}) {
  const label = abbreviation ?? "—";
  const background = color ?? undefined;

  return (
    <span
      title={title}
      style={
        background
          ? {
              backgroundColor: background,
              // The alt colour is usually the team's secondary; when it is
              // missing, white text on a saturated primary is the safer
              // default than a second guess at their palette.
              color: readableOn(background, altColor),
            }
          : undefined
      }
      className={
        "inline-flex min-w-11 items-center justify-center rounded-md px-1.5 py-0.5 " +
        "text-[0.6875rem] font-extrabold tracking-tight " +
        (background ? "" : "bg-panel-inset text-muted")
      }
    >
      {label}
    </span>
  );
}

/**
 * Pick legible text for a team colour.
 *
 * Relative luminance against the sRGB coefficients, thresholded at the usual
 * 0.55. Team primaries run the full range — navy through gold — and a fixed
 * white would vanish on Wyoming or Oregon's yellows. The alt colour is only
 * used when it is itself legible, since plenty of teams pair two dark shades.
 */
function readableOn(background: string, alt: string | null): string {
  const light = luminance(background) > 0.55;
  if (alt) {
    const altLight = luminance(alt) > 0.55;
    if (altLight !== light) return alt;
  }
  // The dark option is the canvas colour, so a chip on a pale team primary
  // reads as a hole punched through to the page rather than as a fourth navy.
  return light ? "#0f172a" : "#ffffff";
}

function luminance(hex: string): number {
  const value = hex.replace("#", "");
  if (value.length !== 6) return 0;
  const r = Number.parseInt(value.slice(0, 2), 16) / 255;
  const g = Number.parseInt(value.slice(2, 4), 16) / 255;
  const b = Number.parseInt(value.slice(4, 6), 16) / 255;
  if (![r, g, b].every(Number.isFinite)) return 0;
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}
