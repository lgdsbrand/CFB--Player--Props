"""AI provider contract for the weekly cached reads.

CLAUDE.md §7 wants a short LLM read per player, generated once a week and
cached (§2, §10 — never per page view). §9 wants unconfirmed vendor choices to
be configuration rather than code. The client has Gemini and Grok keys and no
Anthropic or OpenAI key, so which one runs is `app_config.ai_adapter` and this
file is what every provider satisfies.

Modelled deliberately on `worker/adapters/odds/`, including the two things that
seam got right the hard way:

  * **The null adapter is a real, selectable implementation**, not an error
    path. With `ai_adapter = "none"` the pipeline runs and the player page keeps
    rendering the empty read slot it has had since Phase 4d.
  * **Entitlement and rate limiting are DIFFERENT failures.** The odds probe
    conflated them once and wrote a wrong conclusion into a memo whose whole job
    was to be right. "This key may not do that" is permanent; "not right now"
    clears on its own. They are separate exception types here so a caller cannot
    accidentally treat a retryable pause as a dead end.

Providers are given a finished prompt and return text. Nothing about which
player, which week, or what the read is for reaches this layer — that belongs to
the job, so switching vendor cannot change what the reads say.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


class AiAdapterError(RuntimeError):
    """Raised when a provider cannot serve what was asked of it."""


class AiAuthError(AiAdapterError):
    """The key is rejected, or not entitled to this model.

    Permanent until someone changes the key or the plan. Retrying is pointless
    and a retry loop would turn a clear answer into a timeout.
    """


class AiRateLimitError(AiAdapterError):
    """Too many requests, or the free tier's daily allowance is spent.

    MUST NOT be folded into AiAuthError. A weekly run touches ~1,700 players in
    a burst, so hitting a rate limit is an ordinary event that means "pace
    yourself", not "this key cannot do this". Confusing the two would either
    abandon a working run or hammer a dead one.
    """


class AiSafetyRefusal(AiAdapterError):
    """The provider declined to answer.

    Its own category because it is neither transport nor entitlement: the call
    succeeded and the model chose not to respond. For a sports-analytics prompt
    this should be vanishingly rare, and when it happens it is worth SEEING
    rather than retrying — a prompt that trips a safety filter is a prompt to
    fix, and the job records the player it happened on.
    """


@dataclass(frozen=True)
class ReadResult:
    """One generated read, plus what it cost.

    `model` is the provider's own identifier for what actually answered, not
    what we asked for — providers alias and silently upgrade model names, and
    `ai_reads.model` is meant to record what wrote the row so a change in tone
    can be traced later.
    """

    text: str
    model: str
    tokens_in: int | None = None
    tokens_out: int | None = None

    @property
    def is_empty(self) -> bool:
        return not self.text.strip()


@runtime_checkable
class AiAdapter(Protocol):
    """What every AI provider must offer. Deliberately one method."""

    name: str
    model: str

    def generate(self, prompt: str, *, max_output_tokens: int) -> ReadResult:
        """Turn a finished prompt into a read, or raise."""
        ...
