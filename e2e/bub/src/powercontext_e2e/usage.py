"""Normalize per-call Bub usage without discarding provider-native evidence."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from .models import BubMetrics, CaptureRecord

INPUT_TOKEN_PATHS = (
    ("input_tokens",),
    ("inputTokens",),
    ("prompt_tokens",),
    ("promptTokens",),
)
OUTPUT_TOKEN_PATHS = (
    ("output_tokens",),
    ("outputTokens",),
    ("completion_tokens",),
    ("completionTokens",),
)
TOTAL_TOKEN_PATHS = (("total_tokens",), ("totalTokens",))
CACHED_TOKEN_PATHS = (
    ("cached_input_tokens",),
    ("cache_read_input_tokens",),
    ("input_tokens_details", "cached_tokens"),
    ("prompt_tokens_details", "cached_tokens"),
)
REASONING_TOKEN_PATHS = (
    ("reasoning_tokens",),
    ("output_tokens_details", "reasoning_tokens"),
    ("completion_tokens_details", "reasoning_tokens"),
)


def summarize_bub_metrics(records: Iterable[CaptureRecord]) -> BubMetrics:
    input_tokens = 0
    output_tokens = 0
    cached_input_tokens = 0
    reasoning_tokens = 0
    total_tokens = 0
    llm_calls = 0
    llm_calls_with_usage = 0
    tool_calls = 0

    for record in records:
        if record.event == "tool_result":
            tool_calls += 1
            continue
        if record.event != "llm_result":
            continue

        llm_calls += 1
        if not record.usage:
            continue
        llm_calls_with_usage += 1
        call_input = _first_number(record.usage, INPUT_TOKEN_PATHS)
        call_output = _first_number(record.usage, OUTPUT_TOKEN_PATHS)
        input_tokens += call_input
        output_tokens += call_output
        cached_input_tokens += _first_number(record.usage, CACHED_TOKEN_PATHS)
        reasoning_tokens += _first_number(record.usage, REASONING_TOKEN_PATHS)
        total_tokens += _first_number(record.usage, TOTAL_TOKEN_PATHS, default=call_input + call_output)

    return BubMetrics(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_input_tokens=cached_input_tokens,
        reasoning_tokens=reasoning_tokens,
        total_tokens=total_tokens,
        llm_calls=llm_calls,
        llm_calls_with_usage=llm_calls_with_usage,
        tool_calls=tool_calls,
    )


def _first_number(
    value: Mapping[str, Any],
    paths: tuple[tuple[str, ...], ...],
    *,
    default: int = 0,
) -> int:
    for path in paths:
        current: Any = value
        for part in path:
            if not isinstance(current, Mapping) or part not in current:
                break
            current = current[part]
        else:
            if isinstance(current, int | float) and not isinstance(current, bool):
                return max(0, int(current))
    return default
