"""The no-AI adapter.

A FEATURE, not a stub — the same role `NullOddsAdapter` plays for lines.

`web/lib/data/ai-reads.ts` returns null for a player with no row, and the player
page has rendered that empty state since Phase 4d. That state is not a
placeholder waiting to be removed: it is what a reader sees for any player the
generator skipped, so it has to keep working after generation is switched on.

With `app_config.ai_adapter = "none"` (the default) the weekly job runs, reports
that it is configured off, writes nothing and exits 0. A scheduled canary must
not go red because a deliberate configuration is in force — and until the client
enables billing, "off" is the correct configuration.
"""

from __future__ import annotations

from worker.adapters.ai.base import ReadResult

ADAPTER_NAME = "none"


class NullAiAdapter:
    """Generates nothing, spends nothing, never fails."""

    name = ADAPTER_NAME
    model = "none"

    def generate(self, prompt: str, *, max_output_tokens: int) -> ReadResult:
        """Return an empty result rather than raising.

        Callers check `ReadResult.is_empty` and skip the write. Raising here
        would make "AI is switched off" indistinguishable from "the provider
        failed", which is the distinction the odds seam had to learn twice.
        """
        return ReadResult(text="", model=self.model, tokens_in=0, tokens_out=0)
