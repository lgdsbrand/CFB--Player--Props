"""Tests for the AI provider seam (Phase 5b).

Concentrated on RESPONSES THAT SUCCEED AND CARRY NOTHING USABLE, because that is
the shape that has cost this project real time: CFBD truncating at 2,000 rows,
the odds probe reading an already-kicked game, and here Gemini returning HTTP
200 with the text "Facing the nation'" — a truncated half-word that would have
been cached as a player's read for a week.

All offline. No key, no network, no spend.
"""

from __future__ import annotations

import time

import pytest

from worker.adapters.ai import (
    AiAdapterError,
    AiSafetyRefusal,
    GeminiAdapter,
    GrokAdapter,
    NullAiAdapter,
    get_adapter,
)
from worker.adapters.ai.gemini import _parse as parse_gemini
from worker.adapters.ai.grok import _parse as parse_grok
from worker.adapters.ai.http import AiHttpClient, redact


def _gemini(text: str, finish: str = "STOP", **extra) -> dict:
    return {
        "candidates": [{
            "content": {"parts": [{"text": text}]},
            "finishReason": finish,
        }],
        "usageMetadata": {"promptTokenCount": 421, "candidatesTokenCount": 110,
                          **extra},
        "modelVersion": "gemini-3.6-flash",
    }


class TestGeminiParsing:
    def test_a_normal_response(self):
        result = parse_gemini(_gemini("Allar projects under."),
                              fallback_model="asked-for")
        assert result.text == "Allar projects under."
        assert result.tokens_in == 421 and result.tokens_out == 110

    def test_the_model_that_answered_is_recorded_not_the_one_requested(self):
        """Providers alias and silently upgrade names; ai_reads.model is meant
        to record what actually wrote the row."""
        assert parse_gemini(
            _gemini("x"), fallback_model="asked-for"
        ).model == "gemini-3.6-flash"

    def test_falls_back_to_the_requested_model_when_none_is_reported(self):
        payload = _gemini("x")
        del payload["modelVersion"]
        assert parse_gemini(payload, fallback_model="asked-for").model == "asked-for"

    def test_a_truncated_read_is_refused(self):
        """THE REGRESSION. Gemini 3.x are thinking models and maxOutputTokens
        covers thinking AND text. Measured at a 120-token budget:
        thoughtsTokenCount 116, text 4 tokens, finishReason MAX_TOKENS, and the
        text was "Facing the nation'". It is non-empty, so nothing looks broken,
        and the unique key would keep it in front of readers for a week."""
        with pytest.raises(AiAdapterError, match="truncated"):
            parse_gemini(
                _gemini("Facing the nation'", finish="MAX_TOKENS",
                        thoughtsTokenCount=116),
                fallback_model="m",
            )

    def test_the_truncation_error_names_the_thinking_spend(self):
        with pytest.raises(AiAdapterError, match="116 token"):
            parse_gemini(
                _gemini("Facing the", finish="MAX_TOKENS",
                        thoughtsTokenCount=116),
                fallback_model="m",
            )

    def test_a_blocked_prompt_is_its_own_failure(self):
        with pytest.raises(AiSafetyRefusal):
            parse_gemini({"promptFeedback": {"blockReason": "SAFETY"}},
                         fallback_model="m")

    def test_a_safety_finish_is_its_own_failure(self):
        with pytest.raises(AiSafetyRefusal):
            parse_gemini(_gemini("", finish="SAFETY"), fallback_model="m")

    def test_no_candidates_at_all_raises(self):
        with pytest.raises(AiAdapterError, match="no candidates"):
            parse_gemini({}, fallback_model="m")

    def test_an_empty_read_raises_rather_than_returning_blank(self):
        with pytest.raises(AiAdapterError, match="empty"):
            parse_gemini(_gemini("   "), fallback_model="m")


class TestGrokParsing:
    def test_a_normal_response(self):
        result = parse_grok({
            "choices": [{"message": {"content": "Smith is limited."},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 400, "completion_tokens": 90},
            "model": "grok-4-fast",
        }, fallback_model="asked-for")
        assert result.text == "Smith is limited."
        assert result.model == "grok-4-fast"
        assert result.tokens_in == 400 and result.tokens_out == 90

    def test_a_content_filter_is_a_safety_refusal(self):
        with pytest.raises(AiSafetyRefusal):
            parse_grok({"choices": [{"message": {"content": ""},
                                     "finish_reason": "content_filter"}]},
                       fallback_model="m")

    def test_no_choices_raises(self):
        with pytest.raises(AiAdapterError, match="no choices"):
            parse_grok({}, fallback_model="m")


class TestNullAdapter:
    """Switched off is a DECISION, not a failure."""

    def test_returns_empty_rather_than_raising(self):
        result = NullAiAdapter().generate("anything", max_output_tokens=100)
        assert result.is_empty
        assert result.tokens_in == 0 and result.tokens_out == 0

    def test_selectable_through_the_registry(self):
        assert isinstance(get_adapter("none"), NullAiAdapter)


class TestRegistry:
    def test_known_adapters_build(self):
        assert isinstance(get_adapter("gemini", api_key="k"), GeminiAdapter)
        assert isinstance(get_adapter("grok", api_key="k"), GrokAdapter)

    def test_an_unknown_name_raises_rather_than_silently_disabling_reads(self):
        """A typo must not present as 'reads are switched off'. This job runs
        WEEKLY, so a silent fallback would hide the mistake for seven days."""
        with pytest.raises(AiAdapterError, match="Unknown AI adapter"):
            get_adapter("gemeni")

    def test_an_empty_key_is_refused_at_construction(self):
        with pytest.raises(AiAdapterError, match="GEMINI_API_KEY"):
            GeminiAdapter("")
        with pytest.raises(AiAdapterError, match="GROK_API_KEY"):
            GrokAdapter("")


class TestKeyHygiene:
    """CLAUDE.md §0 treats key hygiene as a hard rule, not a nicety."""

    def test_a_bearer_token_is_redacted(self):
        assert "sk-abcdef123456" not in redact("Authorization: Bearer sk-abcdef123456")

    def test_a_google_key_header_is_redacted(self):
        text = redact("{'x-goog-api-key': 'AIzaSyABCDEFGHIJKLMNOP'}")
        assert "AIzaSyABCDEFGHIJKLMNOP" not in text

    def test_a_key_query_parameter_is_redacted(self):
        assert "AIzaSyABCDEFGHIJ" not in redact("?key=AIzaSyABCDEFGHIJ&x=1")

    def test_ordinary_text_survives(self):
        assert redact("Allar projects under 238.5") == "Allar projects under 238.5"


class TestBackoff:
    """The same defect that took the CFBD ingest down on 2026-08-17 lived here too.

    `Retry-After: 0` is boilerplate on some providers' 429s, not an instruction.
    Obeyed literally it means "retry immediately", so every attempt fires into a
    limiter that is still over its ceiling and the run ends on the rate limit it
    was supposed to wait out.
    """

    def _slept(self, monkeypatch, retry_after: str | None) -> float:
        waits: list[float] = []
        monkeypatch.setattr(time, "sleep", waits.append)
        AiHttpClient(headers={})._sleep_for(1, retry_after=retry_after)
        assert len(waits) == 1
        return waits[0]

    def test_a_zero_retry_after_does_not_skip_the_wait(self, monkeypatch):
        assert self._slept(monkeypatch, "0") > 0

    def test_a_real_retry_after_is_still_obeyed(self, monkeypatch):
        assert self._slept(monkeypatch, "12") == 12.0

    def test_an_unparseable_retry_after_falls_back_to_the_curve(self, monkeypatch):
        assert self._slept(monkeypatch, "in a bit") > 0
