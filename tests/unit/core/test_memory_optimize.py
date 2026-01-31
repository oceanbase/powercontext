import pytest
from unittest.mock import MagicMock
from powermem.core.memory import Memory

def test_memory_optimize_delegation():
    # Setup
    config = {
        "llm": {"provider": "mock"},
        "vector_store": {"provider": "mock"}
    }
    mem = Memory(config=config)

    # Mock internal optimizer
    mem.optimizer = MagicMock()

    # Mock return value
    expected_result = {"deleted": 5}
    mem.optimizer.deduplicate.return_value = expected_result

    # Call
    result = mem.optimize(strategy="deduplicate", user_id="user123")

    # Verify delegation
    mem.optimizer.deduplicate.assert_called_with(
        user_id="user123",
        strategy="exact",
        threshold=0.95
    )
    assert result == expected_result

def test_memory_compress_delegation():
    # Setup
    config = {
        "llm": {"provider": "mock"},
        "vector_store": {"provider": "mock"}
    }
    mem = Memory(config=config)

    # Mock internal optimizer
    mem.optimizer = MagicMock()

    # Mock return value
    expected_result = {"compressed": 3}
    mem.optimizer.compress.return_value = expected_result

    # Call
    result = mem.optimize(strategy="compress", threshold=0.8)

    # Verify delegation
    mem.optimizer.compress.assert_called_with(
        user_id=None,
        threshold=0.8
    )
    assert result == expected_result
