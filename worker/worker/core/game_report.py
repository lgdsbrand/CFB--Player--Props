"""The game model's backtest report (CLAUDE.md §11, G3) — HTML, tables only.

Same house style as docs/calibration-report.html. The verdict paragraph is
computed from the numbers, not written ahead of them: a model that does not
beat the closing line on a market is reported as not beating it.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime

from worker.core.game_backtest import EDGE_THRESHOLD, PRICE_110_PAYOUT

BREAK_EVEN_110 = 1.0 / (1.0 + PRICE_110_PAYOUT)

STYLE = """
<style>
  :root { color-scheme: light dark; }
  body { font: 15px/1.55 -apple-system, "Segoe UI", system-ui, sans-serif;
         max-width: 62rem; margin: 2rem auto; padding: 0 1.25rem; }
  h1 { font-size: 1.6rem; margin-bottom: .2rem; }
  h2 { margin-top: 2.4rem; border-bottom: 1px solid #8884; padding-bottom: .3rem; }
  h3 { margin-top: 1.6rem; font-size: 1.05rem; }
  .sub { opacity: .7; margin-top: 0; }
  table { border-collapse: collapse; width: 100%; font-variant-numeric: tabular-nums; }
  th, td { padding: .4rem .55rem; text-align: right; border-bottom: 1px solid #8883; }
  th:first-child, td.name { text-align: left; }
  th { font-weight: 600; font-size: .82rem; text-transform: uppercase;
        letter-spacing: .04em; opacity: .75; }
  .scroll { overflow-x: auto; }
  .good { color: #15803d; font-weight: 600; }
  .bad  { color: #b91c1c; font-weight: 600; }
  @media (prefers-color-scheme: dark) { .good { color: #4ade80; } .bad { color: #f87171; } }
  .note { background: #8881; border-left: 3px solid #6366f1;
          padding: .8rem 1rem; border-radius: 0 6px 6px 0; margin: .8rem 0; }
  .warn { border-left-color: #f5a623; }
  .verdict { border-left-color: #f43f5e; }
  ul { padding-left: 1.1rem; } li { margin: .4rem 0; }
  code { background: #8882; padding: .1rem .3rem; border-radius: 3px; }
</style>
"""


def _f(x: float, digits: int = 2) -> str:
    return "—" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:.{digits}f}"


def _pct(x: float) -> str:
    return "—" if x is None or math.isnan(x) else f"{100 * x:.1f}%"


def _cls(ok: bool) -> str:
    return "good" if ok else "bad"


def _z_vs_break_even(hit: float, n: int) -> float:
    if not n or math.isnan(hit):
        return float("nan")
    be = BREAK_EVEN_110
    return (hit - be) / math.sqrt(be * (1 - be) / n)


def _accuracy_table(summary: dict, keys: list) -> str:
    rows = []
    for model, entry in summary.items():
        for key in keys:
            a = entry.get(key)
            if not a or not a["games"]:
                continue
            cells = [f"<td class=name>{model} · {key}</td><td>{a['games']}</td>"]
            for ours, market, digits in (
                ("margin_mae", "margin_mae_market", 2),
                ("total_mae", "total_mae_market", 2),
                ("win_brier", "win_brier_market", 4),
            ):
                cells.append(
                    f"<td class={_cls(a[ours] < a[market])}>{_f(a[ours], digits)}</td>"
                    f"<td>{_f(a[market], digits)}</td>"
                )
            rows.append("<tr>" + "".join(cells) + "</tr>")
    return (
        "<div class=scroll><table><tr><th>Model · test</th><th>Games</th>"
        "<th>Margin error</th><th>Market</th><th>Total error</th><th>Market</th>"
        "<th>Win Brier</th><th>Market</th></tr>" + "".join(rows) + "</table></div>"
    )


def _picks_table(summary: dict, keys: list, side: str) -> str:
    rows = []
    for model, entry in summary.items():
        for key in keys:
            a = entry.get(key)
            if not a or not a[side].get("n"):
                continue
            s = a[side]
            z = _z_vs_break_even(s["pick_hit"], s["picks"])
            rows.append(
                f"<tr><td class=name>{model} · {key}</td><td>{s['n']}</td>"
                f"<td>{_f(s['brier'], 4)}</td><td>{_f(s['ece'], 3)}</td>"
                f"<td>{s['picks']}</td>"
                f"<td class={_cls(s['pick_hit'] > BREAK_EVEN_110)}>{_pct(s['pick_hit'])}</td>"
                f"<td class={_cls(s['roi'] > 0)}>{_pct(s['roi'])}</td><td>{_f(z)}</td></tr>"
            )
    return (
        "<div class=scroll><table><tr><th>Model · test</th><th>Graded</th><th>Brier</th>"
        "<th>Cal. error</th><th>Picks</th><th>Hit</th><th>ROI at −110</th>"
        "<th>z vs break-even</th></tr>" + "".join(rows) + "</table></div>"
    )


def _bins_table(side: dict) -> str:
    rows = "".join(
        f"<tr><td class=name>{lo:.2f}–{hi:.2f}</td><td>{n}</td>"
        f"<td>{_pct(pred)}</td><td>{_pct(actual)}</td></tr>"
        for lo, hi, n, pred, actual in side.get("bins", [])
    )
    return (
        "<table><tr><th>Model said</th><th>Games</th><th>Average said</th>"
        "<th>Actually hit</th></tr>" + rows + "</table>"
    )


def _periods_table(summary: dict) -> str:
    rows = []
    for model, entry in summary.items():
        p = entry["all"]["periods"]
        for t in ("q1_margin", "q1_total", "h1_margin", "h1_total", "margin", "total"):
            label = t.replace("q1_", "1Q ").replace("h1_", "1H ")
            cov = p[t]["coverage80"]
            rows.append(
                f"<tr><td class=name>{model} · {label}</td><td>{_f(p[t]['mae'])}</td>"
                f"<td class={_cls(abs(cov - 0.80) <= 0.03)}>{_pct(cov)}</td></tr>"
            )
    return (
        "<table><tr><th>Model · period</th><th>Mean error (pts)</th>"
        "<th>Inside 80% range</th></tr>" + "".join(rows) + "</table>"
    )


def verdict(summary: dict) -> str:
    """Plain-language finding, derived from the pooled numbers."""
    best = min(summary, key=lambda m: summary[m]["all"]["margin_mae"])
    a = summary[best]["all"]
    lines = []
    beats_margin = a["margin_mae"] < a["margin_mae_market"]
    beats_total = a["total_mae"] < a["total_mae_market"]
    beats_win = a["win_brier"] < a["win_brier_market"]
    lines.append(
        f"The better model is <strong>{best}</strong>. Against the closing line it "
        f"{'beats' if beats_margin else 'does <strong>not</strong> beat'} the market on the "
        f"margin ({_f(a['margin_mae'])} vs {_f(a['margin_mae_market'])} points), "
        f"{'beats' if beats_total else 'does <strong>not</strong> beat'} it on the total "
        f"({_f(a['total_mae'])} vs {_f(a['total_mae_market'])}), and "
        f"{'beats' if beats_win else 'does <strong>not</strong> beat'} the vig-free moneyline "
        f"on win probability (Brier {_f(a['win_brier'], 4)} vs {_f(a['win_brier_market'], 4)})."
    )
    for side, label in (("cover", "spread"), ("over", "total")):
        s = a[side]
        z = _z_vs_break_even(s["pick_hit"], s["picks"])
        significant = not math.isnan(z) and z >= 2.0
        lines.append(
            f"{label.capitalize()} picks at a {int(EDGE_THRESHOLD * 100)}% edge: {s['picks']} "
            f"picks hit {_pct(s['pick_hit'])} against {_pct(BREAK_EVEN_110)} break-even "
            f"(ROI {_pct(s['roi'])}, z = {_f(z)}). "
            + (
                "That clears a two-standard-error bar."
                if significant
                else "That is <strong>not</strong> distinguishable from no edge."
            )
        )
    publish = any(
        _z_vs_break_even(a[s]["pick_hit"], a[s]["picks"]) >= 2.0 for s in ("cover", "over")
    )
    lines.append(
        "<strong>Recommendation: "
        + (
            "a market clears the bar; publish it only after a live, frozen-pick forward test "
            "confirms it.</strong>"
            if publish
            else "do not publish picks from this model yet.</strong> Its projections can be "
            "shown as a labelled fair line, but no side of any market has earned an "
            "OVER/UNDER or cover call."
        )
    )
    return "".join(f"<p>{line}</p>" for line in lines)


def render(summary: dict, *, seasons: list, extra: dict | None = None) -> str:
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    keys_season = [k for k in next(iter(summary.values())) if isinstance(k, int)]
    out = [
        "<!doctype html>\n<meta charset=\"utf-8\">",
        "<title>Game model backtest — CFB</title>",
        STYLE,
        "<h1>Game model backtest</h1>",
        f"<p class=sub>College football games &middot; walk-forward test seasons "
        f"{', '.join(str(s) for s in seasons)} &middot; generated {now}</p>",
        "<div class=note><strong>How it was tested.</strong> Each season is predicted by a "
        "model trained only on the seasons before it, and every game only from team data "
        "known before that game's week. The closing line is used to <em>grade</em> and "
        "never as an input. Closing lines are CollegeFootballData's median of ESPN Bet, "
        "DraftKings and Bovada; spreads and totals carry no prices there, so they are "
        "graded at −110 (break-even "
        f"{_pct(BREAK_EVEN_110)}).</div>",
        "<div class=\"note verdict\"><strong>Verdict.</strong>" + verdict(summary) + "</div>",
        "<h2>Accuracy against the closing line</h2>",
        "<p>Mean absolute error in points (lower is better) and Brier score for win "
        "probability (lower is better). Green where the model beats the market.</p>",
        _accuracy_table(summary, ["all", *keys_season, "early", "later"]),
        "<p class=sub><em>early</em> = either team had played two games or fewer that "
        "season; <em>later</em> = both had played three or more.</p>",
        "<h2>Spread picks</h2>",
        _picks_table(summary, ["all", *keys_season, "early", "later"], "cover"),
        "<h2>Total picks</h2>",
        _picks_table(summary, ["all", *keys_season, "early", "later"], "over"),
        "<h2>Calibration</h2>",
        "<p>When the model says a side wins X% of the time, how often did it? A "
        "well-calibrated model's two percentage columns match.</p>",
    ]
    for model, entry in summary.items():
        out.append(f"<h3>{model} · spread (home covers)</h3>")
        out.append(_bins_table(entry["all"]["cover"]))
        out.append(f"<h3>{model} · total (goes over)</h3>")
        out.append(_bins_table(entry["all"]["over"]))
    out += [
        "<h2>First quarter and first half</h2>",
        "<p>No historical 1Q or 1H lines exist, so these cannot be graded against a "
        "market. What can be checked is whether the model's ranges are honest: 80% of "
        "results should fall inside its 80% range.</p>",
        _periods_table(summary),
    ]
    if extra:
        out.append("<h2>2026 to date (never seen in any training)</h2>")
        out.append(_accuracy_table(extra, ["all"]))
        out.append(_picks_table(extra, ["all"], "cover"))
        out.append(_picks_table(extra, ["all"], "over"))
    return "\n".join(out) + "\n"
