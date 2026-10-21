"""The game simulator's backtest report (phase S1): docs/nfl-sim-backtest.html.

Pure rendering of what `jobs/run_sim_backtest.py` measured. Reviewed before
anything is built on it (CLAUDE.md §0, plan S1).
"""

from __future__ import annotations

import html
import math

import polars as pl

from worker.core.live_backtest import minutes_left, score
from worker.core.live_report import STYLE
from worker.core.sim_backtest import LINE_MARKETS, WINNER_MARKETS

SIM = "simulator"
LIVE = "A: live model"


def _f(x, d: int = 3) -> str:
    return "—" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:.{d}f}"


def _pct(x) -> str:
    return "—" if x is None else f"{x * 100:.1f}%"


def pooled(blocks: list[dict]) -> dict[str, dict]:
    """Every test season's moments graded together, per method (in-game moments only)."""
    out = {}
    for name in (
        SIM,
        "simulator, raw win %",
        LIVE,
        "pregame only",
        "nflverse win prob (benchmark)",
    ):
        frames = [b["frames"][name] for b in blocks if name in b["frames"]]
        if frames:
            out[name] = score(
                pl.concat(frames, how="diagonal_relaxed").filter(pl.col("elapsed") > 0)
            )
    return out


def gate(p: dict[str, dict]) -> tuple[bool, str]:
    s, a = p[SIM], p[LIVE]
    ok = s["log_loss"] <= a["log_loss"] and s["brier"] <= a["brier"]
    text = (
        f"Simulator log loss {s['log_loss']:.4f} vs live model {a['log_loss']:.4f}; "
        f"Brier {s['brier']:.4f} vs {a['brier']:.4f}; final margin miss {s['margin_mae']:.2f} vs "
        f"{a['margin_mae']:.2f} points; final total miss {s['total_mae']:.2f} vs {a['total_mae']:.2f}."
    )
    return ok, text


def _table(rows: dict[str, dict], best_key: str = "log_loss") -> str:
    best = min(v[best_key] for v in rows.values())
    out = [
        "<div class='scroll'><table><tr><th>Method</th><th>Moments</th><th>Brier</th><th>Log loss</th>"
        "<th>Final margin miss</th><th>Final total miss</th><th>80% margin range held</th>"
        "<th>80% total range held</th></tr>"
    ]
    for name, o in rows.items():
        cls = " class='best'" if o[best_key] == best else ""
        out.append(
            f"<tr><td class='name'>{html.escape(name)}</td><td>{o['n']:,}</td><td>{_f(o['brier'], 4)}</td>"
            f"<td{cls}>{_f(o['log_loss'], 4)}</td><td>{_f(o.get('margin_mae'), 2)}</td>"
            f"<td>{_f(o.get('total_mae'), 2)}</td><td>{_pct(o.get('margin_cover80'))}</td>"
            f"<td>{_pct(o.get('total_cover80'))}</td></tr>"
        )
    out.append("</table></div>")
    return "".join(out)


def _by_checkpoint(block: dict) -> str:
    names = [n for n in (SIM, LIVE, "nflverse win prob (benchmark)") if n in block["by_checkpoint"]]
    moments = sorted(block["by_checkpoint"][SIM])
    out = ["<div class='scroll'><table><tr><th>Moment</th>"]
    out += [f"<th>{html.escape(n)} Brier</th>" for n in names]
    out += ["<th>Simulator margin miss</th><th>Live model margin miss</th></tr>"]
    for e in moments:
        out.append(f"<tr><td class='name'>{minutes_left(e)}</td>")
        vals = [block["by_checkpoint"][n].get(e, {}).get("brier") for n in names]
        best = min(v for v in vals if v is not None)
        for v in vals:
            out.append(f"<td{' class=best' if v == best else ''}>{_f(v, 4)}</td>")
        s = block["by_checkpoint"][SIM].get(e, {})
        a = block["by_checkpoint"][LIVE].get(e, {})
        out.append(
            f"<td>{_f(s.get('margin_mae'), 2)}</td><td>{_f(a.get('margin_mae'), 2)}</td></tr>"
        )
    out.append("</table></div>")
    return "".join(out)


def _calibration(block: dict) -> str:
    out = ["<div class='scroll'><table><tr><th>Home win % said</th>"]
    for n in (SIM, LIVE):
        out.append(f"<th>{html.escape(n)}: moments</th><th>said</th><th>home won</th>")
    out.append("</tr>")
    cal = {n: {r["bin"]: r for r in block["calibration"][n]} for n in (SIM, LIVE)}
    bins = sorted(set(cal[SIM]) | set(cal[LIVE]), key=lambda b: float(b.split("%")[0]))
    for b in bins:
        out.append(f"<tr><td class='name'>{b}</td>")
        for n in (SIM, LIVE):
            r = cal[n].get(b)
            out.append(
                f"<td>{r['n'] if r else '—'}</td><td>{_pct(r['said']) if r else '—'}</td>"
                f"<td>{_pct(r['won']) if r else '—'}</td>"
            )
        out.append("</tr>")
    out.append("</table></div>")
    return "".join(out)


def _periods(block: dict) -> str:
    rows = block["periods"]
    if not rows:
        return "<p>No pregame moments with period scores.</p>"
    out = [
        "<div class='scroll'><table><tr><th>Market</th><th>Method</th><th>Games</th><th>Brier</th>"
        "<th>Log loss</th><th>Mean miss (points)</th><th>80% range held</th><th>Said over / home</th>"
        "<th>Went over / home</th></tr>"
    ]
    by_market: dict[str, list[dict]] = {}
    for r in rows:
        by_market.setdefault(r["market"], []).append(r)
    for market, rs in by_market.items():
        best = min(r["log_loss"] for r in rs)
        for r in rs:
            cls = " class='best'" if r["log_loss"] == best else ""
            out.append(
                f"<tr><td class='name'>{html.escape(market)}</td><td class='name'>{html.escape(r['method'])}</td>"
                f"<td>{r['n']}</td><td>{_f(r['brier'], 4)}</td><td{cls}>{_f(r['log_loss'], 4)}</td>"
                f"<td>{_f(r['mae'], 2)}</td><td>{_pct(r['cover80'])}</td><td>{_pct(r['said_over'])}</td>"
                f"<td>{_pct(r['went_over'])}</td></tr>"
            )
    out.append("</table></div>")
    for name, label in LINE_MARKETS:
        cal = block["period_cal"].get(name, [])
        if not cal:
            continue
        out.append(
            f"<h3>Is the simulator's {label} over % honest?</h3><div class='scroll'><table>"
            "<tr><th>Said over</th><th>Games</th><th>Average said</th><th>Went over</th></tr>"
        )
        for r in cal:
            out.append(
                f"<tr><td class='name'>{r['bin']}</td><td>{r['n']}</td><td>{_pct(r['said'])}</td><td>{_pct(r['hit'])}</td></tr>"
            )
        out.append("</table></div>")
    return "".join(out)


def _period_verdict(blocks: list[dict]) -> str:
    acc: dict[tuple[str, str], list[float]] = {}
    for b in blocks:
        for r in b["periods"]:
            acc.setdefault((r["market"], r["method"]), []).append(r["log_loss"] * r["n"])
            acc.setdefault((r["market"], r["method"] + "#n"), []).append(r["n"])
    lines = []
    for market in [label for _, label in LINE_MARKETS + WINNER_MARKETS]:
        s = acc.get((market, "simulator"))
        base = acc.get((market, "projection × league share"))
        if not s or not base:
            continue
        n = sum(acc[(market, "simulator#n")])
        ls, lb = sum(s) / n, sum(base) / n
        word = "beats" if ls < lb else "does not beat"
        lines.append(
            f"<li><b>{market}</b>: the simulator {word} the simple method (log loss {ls:.4f} vs {lb:.4f}, {n} games).</li>"
        )
    return "<ul>" + "".join(lines) + "</ul>"


def render(blocks: list[dict]) -> str:
    p = pooled(blocks)
    ok, text = gate(p)
    verdict = (
        "<div class='note'><b>Gate: PASSED.</b> The simulator is at least as good as the live model on "
        f"full-game numbers, so it can supply them. {text}</div>"
        if ok
        else "<div class='note warn'><b>Gate: NOT PASSED.</b> As agreed, the live model keeps the full-game "
        "win %, margin and total; the simulator supplies the animation and the quarter and half numbers "
        f"where those are calibrated below. {text}</div>"
    )
    parts = [
        "<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>",
        "<title>NFL Simulator Backtest</title>",
        STYLE,
        "</head><body>",
        "<h1>NFL game simulator: backtest</h1>",
        "<p class='sub'>Phase S1. Games played out play by play, 1,000 times from each moment, against the live "
        "model on the same moments. No odds anywhere: the team strength is our own pregame projection.</p>",
        verdict,
        "<h3>Every test season together, in-game moments</h3>",
        _table(p),
        "<h3>The first quarter and first half, from kickoff</h3>",
        _period_verdict(blocks),
        "<h2>How to read it</h2><ul>"
        "<li><b>Brier</b> and <b>log loss</b> score the home win %: lower is better; 0.25 / 0.693 is a coin flip.</li>"
        "<li><b>Miss</b> is the average gap between the projected and the actual final margin or total, in points.</li>"
        "<li><b>80% range held</b> should read about 80%.</li>"
        "<li><b>Simulator</b>: every moment played to the end 1,000 times. Each play is a real past NFL play drawn "
        "from the same situation (down, distance, field position, clock), tilted toward better plays for the "
        "stronger offense; punts, field goals, kickoffs and tries after a touchdown from their own fitted rates. "
        "It learns from the four seasons before the test season.</li>"
        "<li><b>Simulator, raw win %</b>: the share of simulated games the home side won, "
        "pulled half a game toward even. <b>Simulator</b> then fits it to who actually won on the season "
        "before with one scale (the live model's win % is also fitted to wins); the margin and total "
        "are the simulated games' own.</li>"
        "<li><b>A: live model</b>: what runs on the site today (the live model backtest's candidate A), fitted on "
        "its earlier seasons exactly as in that report.</li>"
        "<li><b>Projection × league share</b> (quarter and half): the pregame projection times the share of points "
        "a quarter or half carries, with a normal spread around it. The lines graded are the ones that method "
        "would post (its projection rounded to x.5), the way a book sets a line from a number.</li>"
        "<li>The pregame projection for every method is the same Massey rating from games before the week.</li></ul>",
    ]
    for b in blocks:
        parts += [
            f"<h2>Test season {b['test']}</h2>",
            f"<p class='sub'>{b['games']} games, {b['moments']:,} moments (kickoff, every five minutes, three and "
            f"one minute left). Learns from {', '.join(map(str, b['train_seasons']))}. "
            f"{b['dropped']} moments dropped (no matching nflverse snap); {b['mismatch']} with a different score "
            f"in our database than in nflverse. {b['sims']:,} games per moment; {b['seconds'] / 60:.0f} minutes.</p>",
            "<h3>In-game moments</h3>",
            _table(b["overall"]),
            "<h3>At kickoff</h3>",
            _table(b["pregame"]),
            "<h3>Brier by moment of the game</h3>",
            _by_checkpoint(b),
            "<h3>Is the win % honest?</h3>",
            _calibration(b),
            "<h3>First quarter and first half, from kickoff</h3>",
            _periods(b),
            "<h3>Settings fitted for this season</h3><ul>"
            f"<li>Win % scale {next(iter(b['scales'].values())):.3f}, one for the whole game, fitted on "
            f"the season before ({b['cal_sims']} games a moment).</li>"
            f"<li>Team strength spread σθ = {b['sigma']:.2f}, the best pregame log loss on {b['test'] - 1}: "
            + ", ".join(f"{s:.2f} → {v:.4f}" for s, v in b["sigma_table"].items())
            + "</li>"
            f"<li>Quarter adjustments (θ added to both offenses): {', '.join(f'Q{i + 1} {v:+.3f}' for i, v in enumerate(b['offsets']))}, "
            f"fitted so even teams score what the training seasons scored per quarter "
            f"({', '.join(f'{v:.2f}' for v in b['real_q'])}).</li>"
            f"<li>Strength curve (points ~ 1, θ, θ opponent, θ², θ opp², θ·θ opp): {', '.join(f'{c:.2f}' for c in b['curve'])}.</li>"
            "</ul>",
        ]
    parts.append("</body></html>")
    return "\n".join(parts)
