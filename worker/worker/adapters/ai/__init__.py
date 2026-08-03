"""Pluggable AI provider for the weekly cached reads (CLAUDE.md §7, §9).

Which provider runs comes from `app_config.ai_adapter`, so changing vendor — or
turning the reads off entirely — is a row edit rather than a deploy. The client
has Gemini and Grok keys and no Anthropic or OpenAI key.
"""

from __future__ import annotations

from typing import Any

from worker.adapters.ai.base import (
    AiAdapter,
    AiAdapterError,
    AiAuthError,
    AiRateLimitError,
    AiSafetyRefusal,
    ReadResult,
)
from worker.adapters.ai.gemini import ADAPTER_NAME as GEMINI_ADAPTER_NAME
from worker.adapters.ai.gemini import GeminiAdapter
from worker.adapters.ai.grok import ADAPTER_NAME as GROK_ADAPTER_NAME
from worker.adapters.ai.grok import GrokAdapter
from worker.adapters.ai.null import ADAPTER_NAME as NULL_ADAPTER_NAME
from worker.adapters.ai.null import NullAiAdapter

__all__ = [
    "AiAdapter",
    "AiAdapterError",
    "AiAuthError",
    "AiRateLimitError",
    "AiSafetyRefusal",
    "GEMINI_ADAPTER_NAME",
    "GROK_ADAPTER_NAME",
    "GeminiAdapter",
    "GrokAdapter",
    "KNOWN_ADAPTERS",
    "NULL_ADAPTER_NAME",
    "NullAiAdapter",
    "ReadResult",
    "get_adapter",
]

KNOWN_ADAPTERS = (
    NULL_ADAPTER_NAME,
    GEMINI_ADAPTER_NAME,
    GROK_ADAPTER_NAME,
)


def get_adapter(name: str, **kwargs: Any) -> AiAdapter:
    """Build the named adapter.

    Unknown names raise rather than falling back to the null adapter. A silent
    fallback would present "reads are switched off" identically to "your config
    has a typo", and those need different responses — the first is a decision,
    the second is a bug that would otherwise sit undetected for a week at a
    time, since this job runs weekly.
    """
    if name == NULL_ADAPTER_NAME:
        return NullAiAdapter()
    if name == GEMINI_ADAPTER_NAME:
        return GeminiAdapter(**kwargs)
    if name == GROK_ADAPTER_NAME:
        return GrokAdapter(**kwargs)
    raise AiAdapterError(
        f"Unknown AI adapter {name!r}. Known adapters: {list(KNOWN_ADAPTERS)}. "
        "Set app_config.ai_adapter to one of these."
    )
