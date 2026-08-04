"""Tests for the calibration report's computed claims.

The report is the Phase 3 deliverable and the client review gate, so its prose
is not decoration: it states, in words, which direction the model errs. A
sentence that says "too cautious" about an overconfident model would be worse
than no sentence at all, because it reads as a considered finding.

The styling is not tested. The claims are.
"""

from __future__ import annotations

from worker.core.backtest import CalibrationBin, Metrics
from worker.core.report import _tail_note, _worst_bin, render


def _bin(lower: float, predicted: float, observed: float, count: int) -> CalibrationBin:
    return CalibrationBin(
        lower=lower,
        upper=lower + 0.1,
        count=count,
        mean_predicted=predicted,
        observed_rate=observed,
    )


def _metrics(bins: list[CalibrationBin]) -> Metrics:
    return Metrics(
        n=sum(b.count for b in bins),
        base_rate=0.42,
        brier=0.2,
        brier_skill=0.18,
        log_loss=0.61,
        ece=0.04,
        sharpness=0.24,
        bins=bins,
    )


# The shape the real two-season walk produced: near-exact through the middle,
# overconfident at the top.
REAL_SHAPE = [
    _bin(0.0, 0.066, 0.151, 22363),
    _bin(0.6, 0.648, 0.650, 24935),
    _bin(0.9, 0.962, 0.774, 11484),
]


class TestTailNote:
    def test_names_the_worst_bin_and_calls_it_overconfident(self):
        note = _tail_note(_metrics(REAL_SHAPE))
        assert "overconfident" in note
        assert "too cautious" not in note
        assert "0.96" in note and "0.774" in note

    def test_calls_the_opposite_error_too_cautious(self):
        """Direction is derived, not assumed. A model that understates has the
        same magnitude of problem and the opposite name."""
        note = _tail_note(_metrics([
            _bin(0.0, 0.05, 0.06, 5000),
            _bin(0.9, 0.930, 0.990, 5000),
        ]))
        assert "too cautious" in note
        assert "overconfident" not in note

    def test_reports_the_best_calibrated_region_too(self):
        # Refusing to say where the model IS reliable would be its own
        # distortion — the middle is where a real board mostly lives.
        note = _tail_note(_metrics(REAL_SHAPE))
        assert "0.65" in note or "0.648" in note or "0.650" in note

    def test_counts_the_share_sitting_in_the_extreme_bins(self):
        note = _tail_note(_metrics(REAL_SHAPE))
        # 22,363 + 11,484 of 58,782 = 57.6%
        assert "57.6%" in note

    def test_thin_bins_cannot_become_the_headline_claim(self):
        """A 12-row bin at 0.95 that happened to go 0/12 is noise, and quoting
        it as the model's worst failure would be a fabricated finding."""
        note = _tail_note(_metrics([
            _bin(0.0, 0.066, 0.151, 22363),
            _bin(0.6, 0.648, 0.650, 24935),
            _bin(0.9, 0.950, 0.000, 12),
        ]))
        assert "0.000" not in note

    def test_no_note_at_all_when_nothing_is_populated_enough(self):
        assert _tail_note(_metrics([_bin(0.5, 0.55, 0.60, 4)])) == ""

    def test_survives_a_run_with_no_bins(self):
        assert _tail_note(_metrics([])) == ""


class TestWorstBin:
    def test_picks_the_largest_gap_not_the_largest_probability(self):
        text = _worst_bin([
            _bin(0.9, 0.95, 0.94, 1000),
            _bin(0.3, 0.35, 0.55, 1000),
        ])
        assert "0.35" in text and "+0.20" in text

    def test_ignores_bins_thinner_than_thirty(self):
        assert _worst_bin([_bin(0.9, 0.95, 0.10, 29)]) == "—"


class TestRender:
    def test_produces_a_self_contained_page(self):
        html = render(
            overall=_metrics(REAL_SHAPE),
            by_market={"receptions": _metrics(REAL_SHAPE)},
            by_position={"WR": _metrics(REAL_SHAPE)},
            by_phase={"wk1-2 opening": _metrics(REAL_SHAPE)},
            by_transfer={"wk1-2 changed team": _metrics(REAL_SHAPE)},
            by_season={"2024": _metrics(REAL_SHAPE)},
            seasons=[2024, 2025],
            config={"devig_method": "shin"},
            caveats=["<strong>Lines are synthetic.</strong> Something."],
        )
        assert html.startswith("<!doctype html>")
        # CLAUDE.md wants no plotting dependency, so the chart must be inline.
        assert "<svg" in html and "src=" not in html
        assert "http://" not in html and "https://" not in html

    def test_states_plainly_that_edge_is_not_validated(self):
        """The single most important thing the report must not let a reader
        assume. Calibration and profitability are separate claims."""
        html = render(
            overall=_metrics(REAL_SHAPE),
            by_market={}, by_position={}, by_phase={}, by_transfer={},
            by_season={},
            seasons=[2024, 2025], config={}, caveats=[],
        )
        assert "What this does not measure" in html
        assert "edge" in html.lower()

    def test_escapes_a_group_name_rather_than_injecting_it(self):
        html = render(
            overall=_metrics(REAL_SHAPE),
            by_market={"<script>x</script>": _metrics(REAL_SHAPE)},
            by_position={}, by_phase={}, by_transfer={}, by_season={},
            seasons=[2024], config={}, caveats=[],
        )
        assert "<script>x</script>" not in html
        assert "&lt;script&gt;" in html
