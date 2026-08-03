"""xAI Grok adapter.

Built alongside Gemini rather than instead of it, because CLAUDE.md §9 wants an
unconfirmed vendor choice to be a config row and not a deploy. Grok is
OpenAI-compatible, so this same request shape also covers any OpenAI-compatible
endpoint the client might move to later — that generality is free.

**Live X grounding is deliberately not enabled.** It is the one thing that would
distinguish Grok for sports (injury news, depth-chart chatter), and it is
incompatible with how these reads are cached: `ai_reads.input_digest` exists so
a read can be regenerated when its INPUTS change and left alone when they have
not. A model that consults a live feed makes identical inputs produce different
reads, so the digest would stop meaning anything and no read could be
reproduced or audited. If the client ever wants news-aware reads, that is a
different feature with a different cache design, not a flag on this one.
"""

from __future__ import annotations

from typing import Any

from worker.adapters.ai.base import AiAdapterError, AiSafetyRefusal, ReadResult
from worker.adapters.ai.http import AiHttpClient

ADAPTER_NAME = "grok"

BASE_URL = "https://api.x.ai/v1"
DEFAULT_MODEL = "grok-4-fast"
DEFAULT_TEMPERATURE = 0.3


class GrokAdapter:
    """Generates one read per call against xAI's OpenAI-compatible endpoint."""

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
                "GROK_API_KEY is empty. Set it in .env — it is never stored in "
                "the repo, and never in app_config, which is world-readable."
            )
        self.model = model
        self.temperature = temperature
        self._base_url = base_url.rstrip("/")
        self._client = AiHttpClient(
            headers={"Authorization": f"Bearer {api_key}"}
        )

    def generate(self, prompt: str, *, max_output_tokens: int) -> ReadResult:
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_output_tokens,
            "temperature": self.temperature,
        }
        response = self._client.post_json(
            f"{self._base_url}/chat/completions", payload
        )
        return _parse(response, fallback_model=self.model)


def _parse(response: dict[str, Any], *, fallback_model: str) -> ReadResult:
    """Pull text and usage out of an OpenAI-shaped response."""
    choices = response.get("choices") or []
    if not choices:
        raise AiAdapterError(
            f"Grok returned 200 with no choices. Keys present: {sorted(response)}"
        )

    choice = choices[0]
    finish = choice.get("finish_reason")
    text = ((choice.get("message") or {}).get("content") or "").strip()

    if not text:
        if finish == "content_filter":
            raise AiSafetyRefusal(
                "Grok declined to answer (finish_reason=content_filter)"
            )
        raise AiAdapterError(
            f"Grok returned an empty read (finish_reason={finish!r})."
        )

    usage = response.get("usage") or {}
    return ReadResult(
        text=text,
        model=str(response.get("model") or fallback_model),
        tokens_in=_int(usage.get("prompt_tokens")),
        tokens_out=_int(usage.get("completion_tokens")),
    )


def _int(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
