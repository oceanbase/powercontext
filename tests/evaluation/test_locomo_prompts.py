"""Tests for explicit versioned benchmark prompt profiles."""

from benchmark.locomo.prompts import (
    JUDGE_INSTRUCTIONS_VERSION,
    TOPICAL_JUDGE_INSTRUCTIONS_VERSION,
    JudgeProfile,
    judge_instructions,
)


def test_judge_profiles_keep_strict_and_topical_contracts_distinct() -> None:
    strict_prompt, strict_version = judge_instructions(JudgeProfile.STRICT)
    topical_prompt, topical_version = judge_instructions(JudgeProfile.TOPICAL)

    assert strict_version == JUDGE_INSTRUCTIONS_VERSION
    assert topical_version == TOPICAL_JUDGE_INSTRUCTIONS_VERSION
    assert "unsupported additions" in strict_prompt
    assert "touches on the same answer topic" in topical_prompt
    assert strict_prompt != topical_prompt
