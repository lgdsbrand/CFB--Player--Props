"""The live model's backtest report (phase L1): docs/live-model-backtest.html.

Pure rendering of what `jobs/run_live_backtest.py` measured. Reviewed before
anything live is built (CLAUDE.md §0, plan L1).
"""

from __future__ import annotations

import html
import math

from worker.core.live_backtest import minutes_left
from worker.core.live_model import LiveParams

STYLE = """
<style>
  :root { color-scheme: light dark; }
  body { font: 15px/1.55 -apple-system, "Segoe UI", system-ui, sans-serif;
         max-width: 64rem; margin: 2rem auto; padding: 0 1.25rem; }
  h1 { font-size: 1.6rem; margin-bottom: .2rem; }
  h2 { margin-top: 2.4rem; border-bottom: 1px solid #8884; padding-bottom: .3rem; }
  h3 { margin-top: 1.6rem; font-size: 1.05rem; }
  .sub { opacity: .7; margin-top: 0; }
  table { border-collapse: collapse; width: 100%; font-variant-numeric: tabular-nums; }
  th, td { padding: .35rem .5rem; text-align: right; border-bottom: 1px solid #8883; }
  th:first-child, td.name { text-align: left; }
  th { font-weight: 600; font-size: .78rem; text-transform: uppercase;
        letter-spacing: .04em; opacity: .75; }
  .scroll { overflow-x: auto; }
  .best { font-weight: 700; color: #15803d; }
  @media (prefers-color-scheme: dark) { .best { color: #4ade80; } }
  .note { background: #8881; border-left: 3px solid #6366f1;
          padding: .8rem 1rem; border-radius: 0 6px 6px 0; margin: .8rem 0; }
  .warn { border-left-color: #f5a623; }
  ul { padding-left: 1.1rem; } li { margin: .35rem 0; }
  code { background: #8882; padding: .1rem .3rem; border-radius: 3px; }
</style>
"""

SPORT_NAME = {"cfb": "College football", "nfl": "NFL"}
PRIOR_NAME = {
    "cfb": "the college game model's pregame projection, fitted on seasons before the test season",
    "nfl": (
        "a Massey rating from the season's games before the week (the client's fantasy model "
        "has no history to test: CBS keeps none)"
    ),
}


def _f(x: float | None, d: int = 3) -> str:
    return "—" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:.{d}f}"


def _pct(x: float | None) -> str:
    return "—" if x is None else f"{x * 100:.1f}%"


def _overall(block: dict) -> str:
    rows = block["overall"]
    best_ll = min(v["log_loss"] for v in rows.values())
    best_b = min(v["brier"] for v in rows.values())
    out = [
        "<div class='scroll'><table><tr><th>Method</th><th>Checkpoints</th><th>Brier</th>"
        "<th>Log loss</th><th>Final margin miss</th><th>Final total miss</th>"
        "<th>80% margin range held</th><th>80% total range held</th></tr>"
    ]
    for name, v in rows.items():
        out.append(
            f"<tr><td class='name'>{html.escape(name)}</td><td>{v['n']:,}</td>"
            f"<td class='{'best' if v['brier'] == best_b else ''}'>{_f(v['brier'], 4)}</td>"
            f"<td class='{'best' if v['log_loss'] == best_ll else ''}'>{_f(v['log_loss'], 4)}</td>"
            f"<td>{_f(v.get('margin_mae'), 2)}</td><td>{_f(v.get('total_mae'), 2)}</td>"
            f"<td>{_pct(v.get('margin_cover80'))}</td><td>{_pct(v.get('total_cover80'))}</td></tr>"
        )
    out.append("</table></div>")
    return "".join(out)


def _by_checkpoint(block: dict) -> str:
    names = list(block["by_checkpoint"])
    first = block["by_checkpoint"][names[0]]
    head = "".join(f"<th>{html.escape(n.split(':')[0])}</th>" for n in names)
    out = [
        f"<div class='scroll'><table><tr><th>Moment</th>{head}"
        "<th>A margin miss</th><th>A total miss</th><th>A 80% held (margin / total)</th></tr>"
    ]
    a_name = next(n for n in names if n.startswith("A:"))
    for e in sorted(first):
        scores = [block["by_checkpoint"][n].get(e, {}).get("brier") for n in names]
        best = min(s for s in scores if s is not None)
        cells = "".join(
            f"<td class='{'best' if s == best else ''}'>{_f(s, 4)}</td>" for s in scores
        )
        a = block["by_checkpoint"][a_name][e]
        out.append(
            f"<tr><td class='name'>{minutes_left(e)}</td>{cells}"
            f"<td>{_f(a['margin_mae'], 2)}</td><td>{_f(a['total_mae'], 2)}</td>"
            f"<td>{_pct(a['margin_cover80'])} / {_pct(a['total_cover80'])}</td></tr>"
        )
    out.append("</table></div>")
    return "".join(out)


def _calibration(block: dict) -> str:
    parts = []
    for name, bins in block["calibration"].items():
        if not (name.startswith("A:") or name.startswith("B:") or name.startswith("nflverse")):
            continue
        rows = "".join(
            f"<tr><td class='name'>{b['bin']}</td><td>{b['n']:,}</td><td>{_pct(b['said'])}</td>"
            f"<td>{_pct(b['won'])}</td></tr>"
            for b in bins
        )
        parts.append(
            f"<h3>{html.escape(name)}</h3><div class='scroll'><table><tr><th>Home win % said</th>"
            f"<th>Checkpoints</th><th>Average said</th><th>Home won</th></tr>{rows}</table></div>"
        )
    return "".join(parts)


def _params(params: LiveParams) -> str:
    names_m = list(next(iter(params.margin.values())).weights)
    names_t = list(next(iter(params.total.values())).weights)
    head = (
        "".join(f"<th>margin · {n}</th>" for n in names_m) + "<th>margin scale</th><th>tails ν</th>"
        "<th>win k</th>"
        + "".join(f"<th>total · {n}</th>" for n in names_t)
        + "<th>total scale</th><th>tails ν</th>"
    )
    out = [f"<div class='scroll'><table><tr><th>Moment</th>{head}</tr>"]
    for e in sorted(params.margin):
        m, t = params.margin[e], params.total[e]
        nu = lambda v: "normal" if math.isinf(v) else f"{v:.0f}"  # noqa: E731
        out.append(
            f"<tr><td class='name'>{minutes_left(e)}</td>"
            + "".join(f"<td>{m.weights[n]:.3f}</td>" for n in names_m)
            + f"<td>{m.sd_scale:.3f}</td><td>{nu(m.df)}</td><td>{m.win_k:.2f}</td>"
            + "".join(f"<td>{t.weights[n]:.3f}</td>" for n in names_t)
            + f"<td>{t.sd_scale:.3f}</td><td>{nu(t.df)}</td></tr>"
        )
    out.append("</table></div>")
    return "".join(out)


def _ep(block: dict) -> str:
    values = block["ep"].values
    bins = range(10)
    head = "".join(f"<th>{b * 10 + 1}-{min(b * 10 + 10, 99)}</th>" for b in bins)
    rows = "".join(
        f"<tr><td class='name'>Down {d}</td>"
        + "".join(f"<td>{_f(values.get((d, b)), 2)}</td>" for b in bins)
        + "</tr>"
        for d in range(1, 5)
    )
    return f"<div class='scroll'><table><tr><th>Yards to goal</th>{head}</tr>{rows}</table></div>"


#: How much lower B's log loss must be, in EVERY test season, to be worth it.
B_MARGIN = 0.002


def chosen(result: dict) -> str:
    """A, unless B beats it clearly in every test season.

    B needs points added per play during the game, which neither free
    scoreboard (CFBD's, ESPN's) carries: it would mean a play-by-play call
    per game per poll. It has to earn that.
    """
    wins = []
    for b in result["blocks"]:
        ll = {
            name[0]: v["log_loss"] for name, v in b["overall"].items() if name[:2] in ("A:", "B:")
        }
        wins.append(ll["B"] < ll["A"] - B_MARGIN)
    return "B" if wins and all(wins) else "A"


def render(results: list[dict]) -> str:
    body = [
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>Live model backtest</title>",
        STYLE,
        "</head><body>",
        "<h1>Live model backtest</h1>",
        "<p class='sub'>Phase L1: past games frozen every five minutes (plus three and one "
        "minute left), the live model's projection at each moment scored against the final. "
        "No odds anywhere: the prior is our own pregame projection, the state is the "
        "scoreboard.</p>",
        "<div class='note'><b>How to read it.</b> <b>Brier</b> and <b>log loss</b> score the home "
        "win %: lower is better, and 0.25 / 0.693 is a coin flip. <b>Miss</b> is the average "
        "absolute gap between the projected and the actual final margin or total, in points. "
        "<b>80% range held</b> should read about 80%: higher means the ranges are too wide, lower "
        "too narrow. Every number is out of sample: fitted on earlier seasons, scored on the "
        "test season.</div>",
        "<div class='note'><b>The methods.</b><ul>"
        "<li><b>Pregame only</b>: the pregame projection held all game. The floor.</li>"
        "<li><b>Score + clock, no prior</b>: the same model with no team information, so every "
        "game starts even apart from the league's home edge.</li>"
        "<li><b>A</b>: the pregame projection fading with the clock, plus the score and the "
        "possession in progress (expected points from down and field position).</li>"
        "<li><b>B</b>: A plus how the game has played against the pregame view (points added per "
        "play so far, and points ahead of the projection).</li>"
        "<li><b>nflverse win prob</b> (NFL only): nflverse's own published win probability, "
        "their model without the betting line, read at the same snap. An outside benchmark.</li>"
        "</ul>What is left of a game is modelled as a Student t (fatter tails than a normal, "
        "fitted at each moment); the win % is then fitted to who actually won.</div>",
    ]
    for result in results:
        sport = result["sport"]
        pick = chosen(result)
        body.append(f"<h2>{SPORT_NAME[sport]}</h2>")
        body.append(
            f"<p>Prior: {PRIOR_NAME[sport]}. Recommended candidate on these numbers: "
            f"<b>{pick}</b>. B must beat A's log loss by {B_MARGIN} in every test season to "
            "be chosen, because it needs live play-by-play the free scoreboards do not "
            "carry.</p>"
        )
        for block in result["blocks"]:
            train = ", ".join(str(s) for s in block["train"])
            games = f"{block['games']:,} games"
            body.append(f"<h3>Test season {block['test']} · {games} · fitted on {train}</h3>")
            body.append(_overall(block))
            body.append("<h3>Brier by moment of the game</h3>")
            body.append(_by_checkpoint(block))
            body.append("<h3>Is the win % honest?</h3>")
            body.append(_calibration(block))
        last = result["blocks"][-1] if result["blocks"] else None
        if last is not None:
            train = ", ".join(str(s) for s in last["train"])
            body.append(f"<h3>Fitted coefficients, candidate {pick} (fitted on {train})</h3>")
            body.append(_params(last["params"][pick]))
            body.append("<h3>Expected points of the next score, for the team with the ball</h3>")
            body.append(_ep(last))
    body.append("</body></html>")
    return "".join(body)
