"""Unit tests for Ebbinghaus search metadata updates."""

from unittest.mock import MagicMock

import pytest

from powermem.intelligence.plugin import EbbinghausIntelligencePlugin


def make_enabled_plugin() -> EbbinghausIntelligencePlugin:
    plugin = object.__new__(EbbinghausIntelligencePlugin)
    plugin.config = {"enabled": True}
    plugin._importance = None
    plugin._algo = MagicMock()
    return plugin


def test_enhance_for_search_preserves_on_get_metadata_updates():
    plugin = make_enabled_plugin()
    original_metadata = {
        "access_count": 4,
        "memory_type": "working",
        "importance_score": 0.9,
        "search_count": 2,
    }
    memory = {
        "id": "memory-1",
        "metadata": original_metadata,
        "access_count": 4,
        "importance_score": 0.1,
    }
    base_updates = {
        "access_count": 5,
        "metadata": {
            "access_count": 5,
            "memory_type": "short_term",
            "importance_score": 0.9,
            "archived": True,
            "intelligence": {"refreshed": True},
            "search_count": 2,
        },
    }

    enhanced = plugin._enhance_for_search(memory, base_updates)
    metadata = enhanced["metadata"]

    assert metadata["access_count"] == 5
    assert metadata["memory_type"] == "short_term"
    assert metadata["archived"] is True
    assert metadata["intelligence"] == {"refreshed": True}
    assert metadata["search_count"] == 3
    assert "last_searched_at" in metadata
    assert enhanced["search_relevance_score"] == pytest.approx(0.95)


def test_enhance_for_search_does_not_mutate_input_metadata():
    plugin = make_enabled_plugin()
    original_metadata = {"access_count": 4, "search_count": 2}
    base_metadata = {"access_count": 5, "search_count": 2}

    plugin._enhance_for_search(
        {"id": "memory-1", "metadata": original_metadata},
        {"access_count": 5, "metadata": base_metadata},
    )

    assert original_metadata == {"access_count": 4, "search_count": 2}
    assert base_metadata == {"access_count": 5, "search_count": 2}


def test_on_search_keeps_lifecycle_metadata_and_adds_search_metadata():
    plugin = make_enabled_plugin()
    plugin._algo.should_forget.return_value = False
    plugin._algo.should_promote.return_value = True
    plugin._algo.should_archive.return_value = True
    plugin._algo.process_memory_metadata.return_value = {
        "intelligence": {"refreshed": True}
    }
    memory = {
        "id": "memory-1",
        "memory": "User prefers concise updates",
        "metadata": {
            "access_count": 4,
            "memory_type": "working",
            "importance_score": 0.9,
            "search_count": 7,
            "custom": "kept",
        },
    }

    updates, deletes = plugin.on_search([memory])

    assert deletes == []
    assert len(updates) == 1
    mem_id, update = updates[0]
    metadata = update["metadata"]
    assert mem_id == "memory-1"
    assert update["access_count"] == 5
    assert update["memory_type"] == "short_term"
    assert "last_reprocessed_at" in update
    assert metadata["access_count"] == 5
    assert metadata["memory_type"] == "short_term"
    assert metadata["archived"] is True
    assert metadata["intelligence"] == {"refreshed": True}
    assert metadata["custom"] == "kept"
    assert metadata["search_count"] == 8
    assert "last_searched_at" in metadata
    assert update["search_relevance_score"] == pytest.approx(0.95)
