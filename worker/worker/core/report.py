"""Render the calibration report.

Self-contained HTML with inline SVG. No plotting dependency: matplotlib would be
a new package on every Render deploy (CLAUDE.md §0 wants a justification) to draw
one scatter plot and a diagonal, which does not clear that bar. The file opens in
a browser and can be handed to the client as-is.
"""

from __future__ import annotations

import html
from collections.abc import Sequence
from datetime import datetime, timezone

from worker.core.backtest import CalibrationBin, Metrics

PLOT = 320
PAD = 44


def _reliability_svg(metrics: Metrics, title: str) -> str:
    """Reliability diagram: mean predicted probability against observed rate.

    The diagonal is perfect calibration. A point above it means the model was
    too pessimistic at that confidence; below, overconfident. Point area scales
    with the number of predictions, so a wild-looking bin with twelve rows in it
    does not read as loudly as a well-populated one sitting on the line.
    """
    width = PLOT + 2 * PAD
    height = PLOT + 2 * PAD

    def sx(p: float) -> float:
        return PAD + p * PLOT

    def sy(p: float) -> float:
        return PAD + (1.0 - p) * PLOT

    biggest = max((b.count for b in metrics.bins), default=1)
    parts = [
        f'<svg viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="{html.escape(title)}" class="reliability">',
        f'<title>{html.escape(title)}</title>',
    ]

    for step in range(0, 11, 2):
        value = step / 10
        parts.append(
            f'<line x1="{sx(value):.1f}" y1="{PAD}" x2="{sx(value):.1f}" '
            f'y2="{PAD + PLOT}" class="grid"/>'
        )
        parts.append(
            f'<line x1="{PAD}" y1="{sy(value):.1f}" x2="{PAD + PLOT}" '
            f'y2="{sy(value):.1f}" class="grid"/>'
        )
        parts.append(
            f'<text x="{sx(value):.1f}" y="{PAD + PLOT + 16}" '
            f'class="tick" text-anchor="middle">{value:.1f}</text>'
        )
        parts.append(
            f'<text x="{PAD - 8}" y="{sy(value):.1f}" class="tick" '
            f'text-anchor="end" dominant-baseline="middle">{value:.1f}</text>'
        )

    parts.append(
        f'<line x1="{sx(0)}" y1="{sy(0)}" x2="{sx(1)}" y2="{sy(1)}" '
        f'class="ideal"/>'
    )

    points = [
        (sx(b.mean_predicted), sy(b.observed_rate), b)
        for b in metrics.bins
    ]
    if len(points) > 1:
        path = " ".join(
            f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}"
            for i, (x, y, _) in enumerate(points)
        )
        parts.append(f'<path d="{path}" class="curve"/>')

    for x, y, bucket in points:
        radius = 3 + 7 * (bucket.count / biggest) ** 0.5
        parts.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{radius:.1f}" class="pt">'
            f"<title>predicted {bucket.mean_predicted:.3f}, observed "
            f"{bucket.observed_rate:.3f}, n={bucket.count:,}</title></circle>"
        )

    parts.append(
        f'<text x="{PAD + PLOT / 2}" y="{height - 6}" class="axis" '
        f'text-anchor="middle">model probability</text>'
    )
    parts.append(
        f'<text x="14" y="{PAD + PLOT / 2}" class="axis" text-anchor="middle" '
        f'transform="rotate(-90 14 {PAD + PLOT / 2})">observed rate</text>'
    )
    parts.append("</svg>")
    return "".join(parts)


def _tail_note(metrics: Metrics) -> str:
    """Say out loud where the model is reliable and where it is not.

    A single ECE averages the whole curve, and this model's curve is not one
    thing: it is close to exact through the middle and overconfident at both
    ends. Reporting only the aggregate would let a reader conclude the model is
    uniformly well calibrated, which is the opposite of what the extreme bins
    say — and the extreme bins are the ones a board sorted by edge shows first.

    Everything here is computed from the bins, so it stays true as the numbers
    move rather than becoming a stale sentence about an older run.
    """
    populated = [b for b in metrics.bins if b.count >= 30]
    if not populated:
        return ""

    total = sum(b.count for b in metrics.bins)
    worst = max(populated, key=lambda b: abs(b.observed_rate - b.mean_predicted))
    best = min(populated, key=lambda b: abs(b.observed_rate - b.mean_predicted))
    extreme = [b for b in metrics.bins if b.mean_predicted > 0.9 or b.mean_predicted < 0.1]
    extreme_n = sum(b.count for b in extreme)
    worst_gap = worst.observed_rate - worst.mean_predicted

    direction = "overconfident" if worst_gap < 0 else "too cautious"

    return f"""
<div class="note warn" style="margin-top:.8rem">
<strong>Where it is reliable, and where it is not.</strong> The headline ECE
averages the whole curve, and this curve is not uniform. Around
{best.mean_predicted:.2f} the model is close to exact
({best.mean_predicted:.3f} stated against {best.observed_rate:.3f} observed,
{best.observed_rate - best.mean_predicted:+.3f}). At
{worst.mean_predicted:.2f} it is {direction} by
{abs(worst_gap):.3f} &mdash; it states {worst.mean_predicted:.3f} and hits
{worst.observed_rate:.3f} across {worst.count:,} predictions. The projected
distributions have tails that are too thin, so extreme lines draw more certainty
than the outcomes support.
<br><br>
This matters more than the aggregate suggests, because
<strong>the board sorts by edge</strong>: the picks it surfaces first, and the
only ones an EDGES ONLY filter leaves, are drawn disproportionately from exactly
these confident bins. {extreme_n:,} predictions ({extreme_n / total:.1%}) sit
past 0.9 or below 0.1.
<br><br>
<strong>How much to discount it.</strong> Part of this is the synthetic lines
rather than the model: an offset a full standard deviation below a projection
mechanically produces a near-certain over, and a book would not post that line.
The extreme bins are therefore over-represented here relative to a real board,
and the aggregate ECE is correspondingly pessimistic. The direction of the
error is real regardless, and widening the tails is the first thing to try
before this reaches production.
</div>"""


def _metrics_row(name: str, metrics: Metrics) -> str:
    worst = _worst_bin(metrics.bins)
    return (
        "<tr>"
        f"<td class='name'>{html.escape(name)}</td>"
        f"<td>{metrics.n:,}</td>"
        f"<td>{metrics.base_rate:.3f}</td>"
        f"<td>{metrics.brier:.4f}</td>"
        f"<td class='{_skill_class(metrics.brier_skill)}'>"
        f"{metrics.brier_skill:+.3f}</td>"
        f"<td>{metrics.log_loss:.4f}</td>"
        f"<td class='{_ece_class(metrics.ece)}'>{metrics.ece:.4f}</td>"
        f"<td>{metrics.sharpness:.3f}</td>"
        f"<td>{worst}</td>"
        "</tr>"
    )


def _worst_bin(bins: Sequence[CalibrationBin]) -> str:
    populated = [b for b in bins if b.count >= 30]
    if not populated:
        return "—"
    worst = max(populated, key=lambda b: abs(b.mean_predicted - b.observed_rate))
    gap = worst.observed_rate - worst.mean_predicted
    return f"{worst.mean_predicted:.2f}&rarr;{worst.observed_rate:.2f} ({gap:+.2f})"


def _skill_class(value: float) -> str:
    if value > 0.02:
        return "good"
    if value < 0:
        return "bad"
    return ""


def _ece_class(value: float) -> str:
    if value < 0.02:
        return "good"
    if value > 0.05:
        return "bad"
    return ""


def _table(title: str, rows: dict[str, Metrics]) -> str:
    if not rows:
        return ""
    body = "".join(_metrics_row(name, m) for name, m in rows.items())
    return f"""
<h3>{html.escape(title)}</h3>
<div class="scroll">
<table>
  <thead><tr>
    <th>group</th><th>n</th><th>base</th><th>Brier</th><th>skill</th>
    <th>log loss</th><th>ECE</th><th>sharpness</th><th>worst bin</th>
  </tr></thead>
  <tbody>{body}</tbody>
</table>
</div>"""


def render(
    *,
    overall: Metrics,
    by_market: dict[str, Metrics],
    by_position: dict[str, Metrics],
    by_phase: dict[str, Metrics],
    by_season: dict[str, Metrics],
    seasons: Sequence[int],
    config: dict[str, object],
    caveats: Sequence[str],
) -> str:
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    caveat_items = "".join(f"<li>{c}</li>" for c in caveats)
    config_rows = "".join(
        f"<tr><td class='name'>{html.escape(str(k))}</td>"
        f"<td>{html.escape(str(v))}</td></tr>"
        for k, v in sorted(config.items())
    )

    return f"""<!doctype html>
<meta charset="utf-8">
<title>Calibration report — CFB player props</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font: 15px/1.55 -apple-system, "Segoe UI", system-ui, sans-serif;
         max-width: 60rem; margin: 2rem auto; padding: 0 1.25rem; }}
  h1 {{ font-size: 1.6rem; margin-bottom: .2rem; }}
  h2 {{ margin-top: 2.4rem; border-bottom: 1px solid #8884; padding-bottom: .3rem; }}
  h3 {{ margin-top: 1.6rem; font-size: 1.05rem; }}
  .sub {{ opacity: .7; margin-top: 0; }}
  table {{ border-collapse: collapse; width: 100%; font-variant-numeric: tabular-nums; }}
  th, td {{ padding: .4rem .55rem; text-align: right; border-bottom: 1px solid #8883; }}
  th:first-child, td.name {{ text-align: left; }}
  th {{ font-weight: 600; font-size: .82rem; text-transform: uppercase;
        letter-spacing: .04em; opacity: .75; }}
  .scroll {{ overflow-x: auto; }}
  .good {{ color: #15803d; font-weight: 600; }}
  .bad  {{ color: #b91c1c; font-weight: 600; }}
  @media (prefers-color-scheme: dark) {{
    .good {{ color: #4ade80; }} .bad {{ color: #f87171; }}
  }}
  .reliability {{ width: min(408px, 100%); height: auto; }}
  .grid {{ stroke: #8884; stroke-width: 1; }}
  .ideal {{ stroke: #8888; stroke-width: 1.5; stroke-dasharray: 5 4; }}
  .curve {{ fill: none; stroke: #6366f1; stroke-width: 2; }}
  .pt {{ fill: #6366f1cc; stroke: #6366f1; }}
  .tick, .axis {{ font-size: 11px; fill: currentColor; opacity: .7; }}
  .axis {{ font-size: 12px; }}
  .note {{ background: #8881; border-left: 3px solid #6366f1;
           padding: .8rem 1rem; border-radius: 0 6px 6px 0; }}
  .warn {{ border-left-color: #f5a623; }}
  ul {{ padding-left: 1.1rem; }}
  li {{ margin: .4rem 0; }}
  code {{ background: #8882; padding: .1rem .3rem; border-radius: 3px; }}
</style>

<h1>Calibration report</h1>
<p class="sub">College football player props &middot; seasons
{", ".join(str(s) for s in seasons)} &middot; generated {generated}</p>

<div class="note">
<strong>What this measures.</strong> When the model says 60%, does it hit ~60%?
Every prediction is made walk-forward with a knowledge cutoff at the week being
predicted, so no feature draws on the week it is forecasting or anything after it.
</div>

<div class="note warn" style="margin-top:.8rem">
<strong>What this does not measure.</strong> Whether the edge threshold makes
money. Edge is <code>model probability &minus; de-vigged book probability</code>,
and no historical book prices are available &mdash; historical player props sit
behind a paid plan whose monthly allowance is shared with the client's other
models, making a full-season backfill unaffordable. Calibration and edge are
separate claims and this report only supports the first. Edge validation accrues
from live weeks once odds ingestion starts.
</div>

<h2>Overall</h2>
<div class="scroll">
<table>
  <thead><tr>
    <th>group</th><th>n</th><th>base</th><th>Brier</th><th>skill</th>
    <th>log loss</th><th>ECE</th><th>sharpness</th><th>worst bin</th>
  </tr></thead>
  <tbody>{_metrics_row("all predictions", overall)}</tbody>
</table>
</div>

<h3>Reliability</h3>
{_reliability_svg(overall, "Overall reliability")}

{_tail_note(overall)}

<p><strong>Reading it.</strong> The dashed diagonal is perfect calibration.
Points above it mean the model was too cautious at that confidence; below, too
confident. Point size scales with how many predictions landed in the bin, so a
sparse bin does not shout as loudly as a well-populated one.
<strong>ECE</strong> is the average gap between stated and observed probability,
weighted by bin size &mdash; the single number closest to "when it says 60%, does
it hit 60%". <strong>Brier skill</strong> compares against always predicting the
base rate: positive means the model beats that, zero means it adds nothing.
<strong>Sharpness</strong> is how far from a coin flip the model is willing to
go; a perfectly calibrated model that always says 50% is useless, so calibration
and sharpness have to be read together.</p>

<h2>By market</h2>
{_table("Market", by_market)}

<h2>By position</h2>
{_table("Position", by_position)}

<h2>Early vs late season</h2>
<p>College projections lean hardest on priors early, when roster turnover from
the transfer portal means last year's production often happened at another
school and the opponent adjustment is fitted on a barely-connected schedule
graph. Averaging the season together would hide exactly that.</p>
{_table("Phase", by_phase)}

<h2>By season</h2>
{_table("Season", by_season)}

<h2>Caveats</h2>
<ul>{caveat_items}</ul>

<h2>Run configuration</h2>
<div class="scroll">
<table><tbody>{config_rows}</tbody></table>
</div>
"""
