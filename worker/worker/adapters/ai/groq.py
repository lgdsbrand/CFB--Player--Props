"""Groq adapter — the provider the client's own site already runs on.

GROQ IS NOT GROK. Groq is an inference host (api.groq.com) serving open-weight
models; Grok is xAI's model. The client's README lists "Groq (Llama 3 70B)",
and his key is a Groq key. Both happen to speak the OpenAI chat-completions
shape, so this reuses the Grok adapter's response parser rather than a second
copy of it.

THE MODEL IS A SETTING, NOT A CONSTANT, because Groq retires models on short
notice. The client's README names Llama 3 70B; Groq shut down its successor,
`llama-3.3-70b-versatile`, on 2026-08-16 and named `openai/gpt-oss-120b` as the
replacement. `GROQ_MODEL` overrides the default without a deploy of code, only
of an environment variable.

GPT-OSS IS A REASONING MODEL, which needs two things a plain chat model does
not:

  * Its reasoning tokens are spent before the answer, so the completion
    ceiling is the read's own ceiling plus `REASONING_HEADROOM`. Without the
    headroom a long think leaves no room for the read, and the response comes
    back cut off (refused by `_parse`) or empty.
  * `reasoning_effort="low"` and `include_reasoning=False`. A two-sentence read
    needs little thought, and the reasoning text must never land in the read.

Both are sent ONLY to gpt-oss models. Groq rejects reasoning parameters on
models that do not take them, so a `GROQ_MODEL` override to a non-reasoning
model must not inherit them.

No live search or tools: like Grok's X grounding, anything that consults a live
feed makes identical inputs produce different reads, which breaks
`ai_reads.input_digest` (see grok.py).
"""

from __future__ import annotations

from typing import Any

from worker.adapters.ai.base import AiAdapterError, ReadResult
from worker.adapters.ai.grok import _parse
from worker.adapters.ai.http import AiHttpClient

ADAPTER_NAME = "groq"

BASE_URL = "https://api.groq.com/openai/v1"
DEFAULT_MODEL = "openai/gpt-oss-120b"
DEFAULT_TEMPERATURE = 0.3

#: Completion tokens reserved for a reasoning model's thinking, on top of the
#: read's own ceiling. At low effort a short read thinks for a few hundred
#: tokens; this is generous because a refused read costs a player his read for
#: the week, and unused ceiling costs nothing.
REASONING_HEADROOM = 1024

#: Seconds between calls. Groq limits requests and tokens PER MINUTE (unlike
#: Gemini's free tier, which capped requests per day). ~25 a minute stays
#: inside the lowest published tier; the client's tier may allow more, and the
#: HTTP client still backs off on any 429 using Groq's own Retry-After.
MIN_INTERVAL_SECONDS = 2.4


def is_reasoning_model(model: str) -> bool:
    return model.startswith("openai/gpt-oss")


class GroqAdapter:
    """Generates one read per call against Groq's OpenAI-compatible endpoint."""

    name = ADAPTER_NAME

    def __init__(
        self,
        api_key: str,
        *,
        model: str | None = None,
        temperature: float = DEFAULT_TEMPERATURE,
        base_url: str = BASE_URL,
    ) -> None:
        if not api_key:
            raise AiAdapterError(
                "GROQ_API_KEY is empty. Set it on the Render service that runs "
                "generate_ai_reads — never in app_config, which is world-readable."
            )
        self.model = model or DEFAULT_MODEL
        self.temperature = temperature
        self._base_url = base_url.rstrip("/")
        self._client = AiHttpClient(
            headers={"Authorization": f"Bearer {api_key}"},
            min_interval=MIN_INTERVAL_SECONDS,
        )

    def payload(self, prompt: str, *, max_output_tokens: int) -> dict[str, Any]:
        """The request body. Separate from `generate` so it can be tested offline."""
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": self.temperature,
            "max_completion_tokens": max_output_tokens,
        }
        if is_reasoning_model(self.model):
            body["max_completion_tokens"] = max_output_tokens + REASONING_HEADROOM
            body["reasoning_effort"] = "low"
            body["include_reasoning"] = False
        return body

    def generate(self, prompt: str, *, max_output_tokens: int) -> ReadResult:
        response = self._client.post_json(
            f"{self._base_url}/chat/completions",
            self.payload(prompt, max_output_tokens=max_output_tokens),
        )
        return _parse(response, fallback_model=self.model, provider="Groq")
