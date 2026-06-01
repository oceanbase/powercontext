"""Tests for _extract_profile() — structured JSON output and fallback chain."""

import pytest
from unittest.mock import MagicMock, patch


MESSAGES = [{"role": "user", "content": "今天天气很好"}]


@pytest.fixture
def um():
    patches = [
        patch("powermem.user_memory.user_memory.UserProfileStoreFactory"),
        patch("powermem.core.memory.VectorStoreFactory"),
        patch("powermem.core.memory.LLMFactory"),
        patch("powermem.core.memory.EmbedderFactory"),
    ]
    for p in patches:
        p.start()
    from powermem.user_memory.user_memory import UserMemory
    instance = UserMemory()
    instance._get_existing_profile_data = MagicMock(return_value=None)
    yield instance
    for p in patches:
        p.stop()


# --- Layer 1: JSON path ---

def test_json_changed_true_returns_profile(um):
    um._call_llm_for_extraction = MagicMock(
        return_value='{"changed": true, "profile": "用户是一名软件工程师"}'
    )
    assert um._extract_profile(MESSAGES, "u1") == "用户是一名软件工程师"


def test_json_changed_false_returns_empty(um):
    um._call_llm_for_extraction = MagicMock(return_value='{"changed": false}')
    assert um._extract_profile(MESSAGES, "u1") == ""


def test_json_changed_true_empty_profile_returns_empty(um):
    um._call_llm_for_extraction = MagicMock(
        return_value='{"changed": true, "profile": ""}'
    )
    assert um._extract_profile(MESSAGES, "u1") == ""


def test_json_changed_true_missing_profile_key_returns_empty(um):
    um._call_llm_for_extraction = MagicMock(return_value='{"changed": true}')
    assert um._extract_profile(MESSAGES, "u1") == ""


def test_json_embedded_in_prose(um):
    um._call_llm_for_extraction = MagicMock(
        return_value='结果如下：{"changed": true, "profile": "用户喜欢跑步"}'
    )
    assert um._extract_profile(MESSAGES, "u1") == "用户喜欢跑步"


def test_json_keys_english_with_native_language(um):
    um._call_llm_for_extraction = MagicMock(
        return_value='{"changed": true, "profile": "用户是工程师"}'
    )
    assert um._extract_profile(MESSAGES, "u1", native_language="zh") == "用户是工程师"


def test_profile_value_containing_braces(um):
    """json.loads handles braces inside string values; regex fallback would fail."""
    um._call_llm_for_extraction = MagicMock(
        return_value='{"changed": true, "profile": "用户在 {科技公司} 担任工程师"}'
    )
    result = um._extract_profile(MESSAGES, "u1")
    assert "科技公司" in result


# --- Layer 2: exact-match ---

@pytest.mark.parametrize("response", [
    "", '""', "none", "no profile information", "no relevant information",
])
def test_exact_match_noop_returns_empty(um, response):
    um._call_llm_for_extraction = MagicMock(return_value=response)
    assert um._extract_profile(MESSAGES, "u1") == ""


# --- Layer 3: plain text fallback ---

def test_non_json_non_noop_returns_raw_text(um):
    um._call_llm_for_extraction = MagicMock(
        return_value="用户是一名在北京工作的数据工程师，擅长 Python 和 SQL。"
    )
    result = um._extract_profile(MESSAGES, "u1")
    assert "数据工程师" in result


# --- 核心回归：已有 profile 在 no-op 时不被覆盖 ---

def test_existing_profile_not_overwritten_on_noop(um):
    um._get_existing_profile_data = MagicMock(return_value="用户是高级工程师")
    um._call_llm_for_extraction = MagicMock(return_value='{"changed": false}')
    assert um._extract_profile(MESSAGES, "u1") == ""
