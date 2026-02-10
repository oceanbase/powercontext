import pytest
from unittest.mock import MagicMock, call
from powermem.intelligence.memory_optimizer import MemoryOptimizer

@pytest.fixture
def mock_storage():
    return MagicMock()

@pytest.fixture
def mock_llm():
    return MagicMock()

@pytest.fixture
def optimizer(mock_storage, mock_llm):
    return MemoryOptimizer(mock_storage, mock_llm)

def test_deduplicate_exact_match(optimizer, mock_storage):
    # Setup: 3 memories, 2 are identical (same hash)
    memories = [
        {"id": 1, "content": "Hello World", "hash": "hash1", "created_at": "2024-01-01T10:00:00"},
        {"id": 2, "content": "Hello World", "hash": "hash1", "created_at": "2024-01-01T10:05:00"}, # Duplicate, newer
        {"id": 3, "content": "Unique content", "hash": "hash2", "created_at": "2024-01-01T10:10:00"},
    ]

    # Mock get_all_memories to return our list
    mock_storage.get_all_memories.return_value = memories

    # Execute
    stats = optimizer.deduplicate(user_id="user1", strategy="exact")

    # Verify stats
    assert stats["total_checked"] == 3
    assert stats["duplicates_found"] == 1
    assert stats["deleted_count"] == 1

    # Verify delete was called for the duplicate (id 2)
    # Logic should keep the oldest or newest? Usually we keep one.
    # Let's assume we keep the oldest (id 1) and delete newer ones (id 2).
    mock_storage.delete_memory.assert_called_once_with(2, user_id="user1")

def test_calculate_similarity():
    # Test identical vectors
    v1 = [1.0, 0.0, 0.0]
    v2 = [1.0, 0.0, 0.0]
    assert MemoryOptimizer._cosine_similarity(v1, v2) == 1.0

    # Test orthogonal vectors
    v3 = [0.0, 1.0, 0.0]
    assert MemoryOptimizer._cosine_similarity(v1, v3) == 0.0

    # Test opposite vectors
    v4 = [-1.0, 0.0, 0.0]
    assert MemoryOptimizer._cosine_similarity(v1, v4) == -1.0

    # Test zero vector handling (should return 0.0 to avoid division by zero)
    v5 = [0.0, 0.0, 0.0]
    assert MemoryOptimizer._cosine_similarity(v1, v5) == 0.0

def test_deduplicate_semantic(optimizer, mock_storage):
    # Setup: 3 memories with embeddings
    # id 1 and id 2 are semantically similar (e.g., sim=0.99)
    # id 3 is distinct
    memories = [
        {"id": 1, "content": "Hello", "embedding": [1.0, 0.0], "created_at": "2024-01-01T10:00:00"},
        {"id": 2, "content": "Hi there", "embedding": [0.99, 0.01], "created_at": "2024-01-01T10:05:00"}, # Similar to 1
        {"id": 3, "content": "Apple", "embedding": [0.0, 1.0], "created_at": "2024-01-01T10:10:00"},
    ]

    mock_storage.get_all_memories.return_value = memories
    mock_storage.delete_memory.return_value = True

    # Execute with threshold 0.9
    stats = optimizer.deduplicate(user_id="user1", strategy="semantic", threshold=0.9)

    # Verify stats
    assert stats["total_checked"] == 3
    assert stats["duplicates_found"] == 1
    assert stats["deleted_count"] == 1

    # Verify duplicate (id 2) is deleted (keeping older id 1)
    mock_storage.delete_memory.assert_called_once_with(2, user_id="user1")

def test_compress_logic(optimizer, mock_storage, mock_llm):
    # Setup: 3 memories. 2 similar, 1 distinct.
    memories = [
        {"id": 1, "content": "The cat sat on the mat.", "embedding": [1.0, 0.0, 0.0]},
        {"id": 2, "content": "A cat is sitting on a mat.", "embedding": [0.99, 0.01, 0.0]}, # Similar to 1
        {"id": 3, "content": "I like coding in Python.", "embedding": [0.0, 0.0, 1.0]},
    ]
    mock_storage.get_all_memories.return_value = memories

    # Mock LLM response
    mock_llm.generate_response.return_value = "Compressed: Cat on mat."

    # Execute
    stats = optimizer.compress(user_id="user1", threshold=0.9)

    # Verify
    assert stats["total_processed"] == 3
    assert stats["clusters_found"] == 1 # (1, 2)
    assert stats["new_memories_created"] == 1
    assert stats["compressed_count"] == 2

    # Check LLM call
    mock_llm.generate_response.assert_called_once()

    # Check Storage calls
    # Should add 1 new memory
    mock_storage.add_memory.assert_called_once()
    args, kwargs = mock_storage.add_memory.call_args
    assert args[0]["content"] == "Compressed: Cat on mat."

    # Should delete 2 old memories (id 1 and 2)
    assert mock_storage.delete_memory.call_count == 2
    # Verify calls regardless of order
    mock_storage.delete_memory.assert_has_calls([call(1, user_id="user1"), call(2, user_id="user1")], any_order=True)
