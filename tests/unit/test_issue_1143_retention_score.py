"""Unit tests for Issue #1143 — retention_score null fix (FC-4).

Verifies that _persist_memory_to_storage() correctly extracts retention_score
from intelligence.current_retention instead of using None.

The current code uses memory_data.get('retention_score') which returns None
because memory_data doesn't have that key at the top level. The fix should
extract from memory_data['metadata']['intelligence']['current_retention'].
"""

from unittest.mock import MagicMock, patch, PropertyMock
from enum import Enum

import pytest


class _FakeScope(Enum):
    AGENT = "agent"
    USER = "user"


class _FakeMemoryType(Enum):
    LONG_TERM = "long_term"


def _make_memory_data_agent(intelligence=None, top_level_retention=None):
    """Build a memory_data dict similar to what process_memory() creates."""
    data = {
        'content': 'test content',
        'agent_id': 'test_agent',
        'scope': _FakeScope.AGENT,
        'memory_type': _FakeMemoryType.LONG_TERM,
        'metadata': {},
    }
    if intelligence is not None:
        data['metadata']['intelligence'] = intelligence
    if top_level_retention is not None:
        data['retention_score'] = top_level_retention
    return data


def _make_memory_data_user(intelligence=None, top_level_retention=None):
    """Build a memory_data dict for multi_user."""
    data = {
        'content': 'test content',
        'user_id': 'test_user',
        'scope': _FakeScope.USER,
        'memory_type': _FakeMemoryType.LONG_TERM,
        'metadata': {},
    }
    if intelligence is not None:
        data['metadata']['intelligence'] = intelligence
    if top_level_retention is not None:
        data['retention_score'] = top_level_retention
    return data


def _capture_metadata_from_persist(manager, memory_data):
    """Call _persist_memory_to_storage and capture the metadata dict
    passed to Memory.add(). Returns the metadata dict or None."""
    mock_instance = MagicMock()
    mock_instance.add.return_value = {
        'results': [{'id': 99999}]
    }

    with patch.object(manager, '_get_or_create_memory_instance', return_value=mock_instance):
        try:
            manager._persist_memory_to_storage(memory_data)
        except Exception:
            pass

    if mock_instance.add.called:
        call_kwargs = mock_instance.add.call_args.kwargs
        return call_kwargs.get('metadata')
    return None


class TestIssue1143MultiAgentRetentionScore:
    """FC-4: multi_agent._persist_memory_to_storage retention_score."""

    def test_retention_score_extracted_from_intelligence(self):
        """AC-4.1: retention_score is extracted from intelligence.current_retention."""
        from powermem.agent.implementations.multi_agent import MultiAgentMemoryManager

        manager = MultiAgentMemoryManager.__new__(MultiAgentMemoryManager)
        memory_data = _make_memory_data_agent(intelligence={
            'current_retention': 0.85,
            'importance_score': 0.7,
        })

        metadata = _capture_metadata_from_persist(manager, memory_data)

        assert metadata is not None, "Memory.add() was not called"
        assert metadata.get('retention_score') is not None, (
            "retention_score should not be None — "
            "should be extracted from intelligence.current_retention"
        )
        assert metadata['retention_score'] == pytest.approx(0.85), (
            "retention_score should equal intelligence.current_retention (0.85)"
        )

    def test_retention_score_defaults_to_1_when_intelligence_missing(self):
        """AC-4.2: retention_score defaults to 1.0 when intelligence is missing."""
        from powermem.agent.implementations.multi_agent import MultiAgentMemoryManager

        manager = MultiAgentMemoryManager.__new__(MultiAgentMemoryManager)
        memory_data = _make_memory_data_agent()  # No intelligence

        metadata = _capture_metadata_from_persist(manager, memory_data)

        assert metadata is not None, "Memory.add() was not called"
        assert metadata.get('retention_score') == pytest.approx(1.0), (
            "retention_score should default to 1.0 when intelligence is missing"
        )

    def test_retention_score_defaults_when_current_retention_missing(self):
        """AC-4.3: retention_score defaults to 1.0 when current_retention not in intelligence."""
        from powermem.agent.implementations.multi_agent import MultiAgentMemoryManager

        manager = MultiAgentMemoryManager.__new__(MultiAgentMemoryManager)
        memory_data = _make_memory_data_agent(intelligence={
            'importance_score': 0.7,
            # No current_retention
        })

        metadata = _capture_metadata_from_persist(manager, memory_data)

        assert metadata is not None
        assert metadata.get('retention_score') == pytest.approx(1.0), (
            "retention_score should default to 1.0 when current_retention is missing"
        )


class TestIssue1143MultiUserRetentionScore:
    """FC-4: multi_user._persist_memory_to_storage retention_score."""

    def test_retention_score_extracted_from_intelligence(self):
        """AC-4.4: multi_user extracts retention_score from intelligence.current_retention."""
        from powermem.agent.implementations.multi_user import MultiUserMemoryManager

        manager = MultiUserMemoryManager.__new__(MultiUserMemoryManager)
        memory_data = _make_memory_data_user(intelligence={
            'current_retention': 0.72,
            'importance_score': 0.6,
        })

        metadata = _capture_metadata_from_persist(manager, memory_data)

        assert metadata is not None, "Memory.add() was not called"
        assert metadata.get('retention_score') is not None, (
            "retention_score should not be None in multi_user"
        )
        assert metadata['retention_score'] == pytest.approx(0.72)

    def test_retention_score_defaults_when_no_intelligence(self):
        """AC-4.5: multi_user defaults retention_score to 1.0 when intelligence missing."""
        from powermem.agent.implementations.multi_user import MultiUserMemoryManager

        manager = MultiUserMemoryManager.__new__(MultiUserMemoryManager)
        memory_data = _make_memory_data_user()

        metadata = _capture_metadata_from_persist(manager, memory_data)

        assert metadata is not None
        assert metadata.get('retention_score') == pytest.approx(1.0)


class TestIssue1143RetentionScoreNullSafety:
    """FC-4: retention_score null safety checks (always pass — verify pattern)."""

    def test_metadata_intelligence_chain_safety(self):
        """The nested dict access pattern handles missing keys gracefully."""
        memory_data = {
            'metadata': {
                'intelligence': {
                    'current_retention': 0.9,
                }
            }
        }
        result = memory_data.get('metadata', {}).get('intelligence', {}).get('current_retention', 1.0)
        assert result == pytest.approx(0.9)

    def test_metadata_intelligence_missing_defaults(self):
        """Missing intelligence dict defaults to 1.0."""
        memory_data = {'metadata': {}}
        result = memory_data.get('metadata', {}).get('intelligence', {}).get('current_retention', 1.0)
        assert result == pytest.approx(1.0)

    def test_metadata_empty_defaults(self):
        """Empty metadata defaults to 1.0."""
        memory_data = {}
        result = memory_data.get('metadata', {}).get('intelligence', {}).get('current_retention', 1.0)
        assert result == pytest.approx(1.0)
