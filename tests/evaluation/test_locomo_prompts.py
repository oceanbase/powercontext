"""Tests for explicit versioned benchmark prompt profiles."""

import pytest

from benchmark.locomo.prompts import (
    ANSWER_SOURCE_INFERENCE_INSTRUCTIONS_VERSION,
    ANSWER_SOURCE_UNKNOWN_FALLBACK_INSTRUCTIONS_VERSION,
    JUDGE_INSTRUCTIONS_VERSION,
    TOPICAL_JUDGE_INSTRUCTIONS_VERSION,
    JudgeProfile,
    answer_instructions,
    answer_policy_version,
    judge_instructions,
)


def test_inference_aware_answer_policy_is_explicit_and_requires_source_content() -> None:
    prompt, version = answer_instructions(source_content=True, inference_aware=True)

    assert version == ANSWER_SOURCE_INFERENCE_INSTRUCTIONS_VERSION
    assert 'Do not answer "Unknown"' in prompt
    assert "because the conclusion is implicit" in prompt
    assert "one decisive supporting fact" in prompt
    with pytest.raises(ValueError, match="requires Source expansion"):
        answer_instructions(source_content=False, inference_aware=True)


def test_unknown_fallback_answer_policy_is_explicit_and_mutually_exclusive() -> None:
    version = answer_policy_version(source_content=True, unknown_fallback_inference=True)

    assert version == ANSWER_SOURCE_UNKNOWN_FALLBACK_INSTRUCTIONS_VERSION
    with pytest.raises(ValueError, match="requires Source expansion"):
        answer_policy_version(source_content=False, unknown_fallback_inference=True)
    with pytest.raises(ValueError, match="mutually exclusive"):
        answer_policy_version(source_content=True, inference_aware=True, unknown_fallback_inference=True)


def test_judge_profiles_keep_strict_and_topical_contracts_distinct() -> None:
    strict_prompt, strict_version = judge_instructions(JudgeProfile.STRICT)
    topical_prompt, topical_version = judge_instructions(JudgeProfile.TOPICAL)

    assert strict_version == JUDGE_INSTRUCTIONS_VERSION
    assert topical_version == TOPICAL_JUDGE_INSTRUCTIONS_VERSION
    assert "unsupported additions" in strict_prompt
    assert "touches on the same answer topic" in topical_prompt
    assert strict_prompt != topical_prompt
