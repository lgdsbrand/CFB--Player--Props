"""Google Gemini adapter.

Recommended provider for the weekly reads. Not on cost — at ~1,700 players a
week and roughly 800 input tokens each, both vendors land between cents and a
few dollars, and a case built on that difference would be noise. The reasons
that actually decide it:

  * a free tier the whole pipeline can be proven on before the client pays;
  * a weekly BURST of latency-insensitive calls is the shape batch pricing
    exists for;
  * Grok's distinguishing feature is live X grounding, which we would have to
    switch OFF — identical inputs producing different reads breaks
    `ai_reads.input_digest` and makes a read impossible to reproduce.

WORTH TELLING THE CLIENT: Google's FREE tier trains on submitted content and the
paid tier does not. These prompts carry the client's model projections.
"""

from __future__ import annotations

from typing import Any

from worker.adapters.ai.base import (
    AiAdapterError,
    AiSafetyRefusal,
    ReadResult,
)
from worker.adapters.ai.http import AiHttpClient

ADAPTER_NAME = "gemini"

BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

# A Flash-tier model: the reads are short, formulaic and high-volume, which is
# exactly what the small models are for. Confirmed available on the client's key
# on 2026-08-03 by listing /v1beta/models rather than trusting a docs page.
#
# PINNED, not `gemini-flash-latest`. An alias that silently moves would make
# reads generated in different weeks incomparable and an unexplained change in
# tone impossible to trace — and `ai_reads.prompt_version` only records OUR side
# of that. Overridable so a bump is a config change, not a deploy.
DEFAULT_MODEL = "gemini-3.6-flash"

# Deterministic-ish. These reads restate a projection the board already shows,
# so variation between runs is noise that makes `input_digest` less meaningful.
DEFAULT_TEMPERATURE = 0.3


class GeminiAdapter:
    """Generates one read per call against Gemini's REST API."""

    name = ADAPTER_NAME

    def __init__(
        self,
        api_key: str,
        *,
        model: str = DEFAULT_MODEL,
        temperature: float = DEFAULT_TEMPERATURE,
        base_url: str = BASE_URL,
    ) -> None:
        if not api_key:
            raise AiAdapterError(
                "GEMINI_API_KEY is empty. Set it in .env — it is never stored "
                "in the repo, and never in app_config, which is world-readable."
            )
        self.model = model
        self.temperature = temperature
        self._base_url = base_url.rstrip("/")
        self._client = AiHttpClient(headers={"x-goog-api-key": api_key})

    def generate(self, prompt: str, *, max_output_tokens: int) -> ReadResult:
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "maxOutputTokens": max_output_tokens,
                "temperature": self.temperature,
                # THINKING IS TURNED DOWN, AND THIS IS NOT AN OPTIMISATION.
                #
                # Gemini 3.x are reasoning models and `maxOutputTokens` is a
                # budget for thinking AND text TOGETHER. Measured on 2026-08-03
                # against this key, 200-token budget, same prompt:
                #
                #   default              thoughts=190  text=6   MAX_TOKENS
                #   thinkingLevel=low    thoughts=190  text=6   MAX_TOKENS
                #   thinkingLevel=minimal thoughts=0   text=35  STOP
                #
                # At a 120-token budget the default returned HTTP 200 carrying
                # "Facing the nation'" — a truncated half-word that would have
                # been cached as a player's read for a week.
                #
                # So "minimal", not "low": low is not low. These reads restate a
                # projection the board already computed and are handed every
                # number they need, so there is nothing to reason about and the
                # budget belongs to the prose. It is also most of the cost —
                # thinking is billed as output, the expensive side.
                #
                # NOTE: `thinkingBudget: 0` is REJECTED with HTTP 400 by this
                # model. The knob is thinkingLevel.
                "thinkingConfig": {"thinkingLevel": "minimal"},
            },
        }
        url = f"{self._base_url}/models/{self.model}:generateContent"
        response = self._client.post_json(url, payload)
        return _parse(response, fallback_model=self.model)


def _parse(response: dict[str, Any], *, fallback_model: str) -> ReadResult:
    """Pull the text and usage out of a Gemini response.

    Gemini can return HTTP 200 with NO text at all — a prompt blocked by a
    safety filter, or a candidate that hit the token ceiling before emitting
    anything. Both are 200s carrying nothing, the exact shape this project has
    been caught by three times, so each gets named rather than silently becoming
    an empty read.
    """
    block = (response.get("promptFeedback") or {}).get("blockReason")
    if block:
        raise AiSafetyRefusal(f"Gemini blocked the prompt: {block}")

    candidates = response.get("candidates") or []
    if not candidates:
        raise AiAdapterError(
            "Gemini returned 200 with no candidates and no block reason. "
            f"Keys present: {sorted(response)}"
        )

    candidate = candidates[0]
    finish = candidate.get("finishReason")
    parts = ((candidate.get("content") or {}).get("parts")) or []
    text = "".join(p.get("text", "") for p in parts).strip()

    if not text:
        if finish == "SAFETY":
            raise AiSafetyRefusal("Gemini declined to answer (finishReason=SAFETY)")
        raise AiAdapterError(
            f"Gemini returned an empty read (finishReason={finish!r}). "
            "MAX_TOKENS here means the budget was spent before any text was "
            "emitted, which is a prompt problem, not a transport one."
        )

    usage = response.get("usageMetadata") or {}

    # A TRUNCATED READ IS WORSE THAN NO READ. It is non-empty, so nothing about
    # it looks broken; it renders as a confident sentence that stops mid-word,
    # and the unique key on (player, season, week) means it stays there for a
    # week. Refuse it so the job records a failure it can retry rather than
    # caching a fragment.
    if finish == "MAX_TOKENS":
        thoughts = _int(usage.get("thoughtsTokenCount")) or 0
        raise AiAdapterError(
            f"Gemini truncated the read at the token ceiling "
            f"(finishReason=MAX_TOKENS, {thoughts} token(s) spent on thinking). "
            f"Raise max_output_tokens or keep thinkingBudget at 0. "
            f"Truncated text began: {text[:60]!r}"
        )
    return ReadResult(
        text=text,
        # What ANSWERED, not what was asked for: providers alias and silently
        # upgrade model names, and ai_reads.model is meant to record the writer.
        model=str(response.get("modelVersion") or fallback_model),
        tokens_in=_int(usage.get("promptTokenCount")),
        tokens_out=_int(usage.get("candidatesTokenCount")),
    )


def _int(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
