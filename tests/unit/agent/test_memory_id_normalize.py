"""Unit tests for memory id normalization helpers."""

import pytest

from powermem.agent.utils.memory_id import memory_key_variants, normalize_memory_id


def test_normalize_memory_id_accepts_int_and_string():
    assert normalize_memory_id(123) == 123
    assert normalize_memory_id("123") == 123
    assert normalize_memory_id(" 456 ") == 456


def test_memory_key_variants_include_int_and_str():
    assert memory_key_variants(123) == [123, "123"]


def test_normalize_memory_id_rejects_empty_string():
    with pytest.raises(ValueError):
        normalize_memory_id("")


def test_normalize_memory_id_rejects_invalid_type():
    with pytest.raises(ValueError):
        normalize_memory_id(True)
