"""The no-odds adapter.

This is a FEATURE, not a stub. CLAUDE.md §7 requires the model to run on all
projected starters and high-usage skill players regardless of whether a book has
posted a line — college books post props late, often Thursday or Friday for
Saturday games. §9.1 additionally requires the app to degrade gracefully if the
odds plan turns out not to cover NCAAF player props at all.

With `app_config.odds_adapter = "none"` (the current default) the pipeline runs
end to end and every pick carries a model lean with `line_id`, `book_prob_over`
and `edge` left NULL — which the schema already distinguishes from zero edge.
Selecting this adapter is how you verify that path stays working.
"""

from __future__ import annotations

from worker.adapters.odds.base import OddsEvent, PropQuote, QuotaSnapshot

ADAPTER_NAME = "none"


class NullOddsAdapter:
    """Serves no lines, consumes no quota, never fails."""

    name = ADAPTER_NAME

    @property
    def quota(self) -> QuotaSnapshot:
        return QuotaSnapshot()

    def list_events(self, *, days_ahead: int | None = None) -> list[OddsEvent]:
        return []

    def fetch_props(
        self, event_id: str, market_keys: list[str] | None = None
    ) -> list[PropQuote]:
        return []
