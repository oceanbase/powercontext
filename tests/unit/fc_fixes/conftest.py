"""
Shared fixtures for FC fix tests (Phase 4 — powermem issue batch fix).

All external dependencies (database, LLM, HTTP) are mocked.
"""

import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from datetime import datetime, timezone, timedelta


# ─── FC-3: OceanBase forget marker ────────────────────────────────────

@pytest.fixture
def mock_get_current_datetime():
    """Return a fixed datetime for deterministic tests."""
    fixed = datetime(2026, 7, 25, 12, 0, 0, tzinfo=timezone.utc)
    with patch("powermem.core.memory.get_current_datetime", return_value=fixed):
        yield fixed


@pytest.fixture
def mock_storage():
    """Mock storage backend that records update_memory() calls."""
    storage = MagicMock()
    storage.update_memory = MagicMock()
    storage.search = MagicMock(return_value={"results": []})
    storage.get_all = MagicMock(return_value={"results": []})
    return storage


# ─── FC-4: retention_score ────────────────────────────────────────────

@pytest.fixture
def mock_memory_instance():
    """Mock Memory instance whose add() returns a Snowflake ID."""
    mem = MagicMock()
    mem.add.return_value = {"results": [{"id": 1234567890123456789}]}
    return mem


@pytest.fixture
def sample_memory_data_multi_agent():
    """Memory data dict as built by multi_agent process_memory()."""
    return {
        "content": "User prefers dark mode",
        "user_id": "u1",
        "agent_id": "a1",
        "run_id": "r1",
        "scope": MagicMock(value="agent"),
        "memory_type": MagicMock(value="long_term"),
        "metadata": {
            "intelligence": {
                "current_retention": 0.85,
                "importance_score": 0.7,
                "memory_type": "long_term",
            },
            "enhanced": True,
        },
    }


@pytest.fixture
def sample_memory_data_no_intelligence():
    """Memory data dict without intelligence (LLM disabled)."""
    return {
        "content": "Some content",
        "user_id": "u1",
        "agent_id": "a1",
        "run_id": "r1",
        "scope": MagicMock(value="agent"),
        "memory_type": MagicMock(value="long_term"),
        "metadata": {},
    }


# ─── FC-5: API security ──────────────────────────────────────────────

@pytest.fixture
def mock_logger():
    """Mock logger to verify log calls."""
    with patch("server.services.memory_service.logger") as ml, \
         patch("server.services.user_service.logger") as ul, \
         patch("server.services.agent_service.logger") as al, \
         patch("server.services.search_service.logger") as sl, \
         patch("server.utils.health_check.logger") as hl:
        yield {
            "memory": ml,
            "user": ul,
            "agent": al,
            "search": sl,
            "health": hl,
        }


# ─── FC-6: Importance evaluator ──────────────────────────────────────

@pytest.fixture
def evaluator():
    """ImportanceEvaluator with default weights, no LLM."""
    from powermem.intelligence.importance_evaluator import ImportanceEvaluator
    config = MagicMock()
    llm_config = MagicMock()
    ev = ImportanceEvaluator(config, llm_config)
    ev.llm = None  # Force rule-based path
    return ev


@pytest.fixture
def evaluator_custom_weights():
    """ImportanceEvaluator with custom weights."""
    from powermem.intelligence.importance_evaluator import ImportanceEvaluator
    config = MagicMock()
    llm_config = MagicMock()
    ev = ImportanceEvaluator(config, llm_config)
    ev.llm = None
    ev.criteria_weights = {
        "relevance": 0.5,
        "novelty": 0.2,
        "emotional_impact": 0.1,
        "actionable": 0.1,
        "factual": 0.05,
        "personal": 0.05,
    }
    return ev


# ─── FC-7: Ebbinghaus decay ──────────────────────────────────────────

@pytest.fixture
def ebbinghaus_config():
    """Standard Ebbinghaus configuration."""
    return {
        "initial_retention": 1.0,
        "decay_rate": 1.5,
        "reinforcement_factor": 0.3,
        "working_threshold": 0.3,
        "short_term_threshold": 0.6,
        "long_term_threshold": 0.8,
    }


@pytest.fixture
def ebbinghaus(ebbinghaus_config):
    """EbbinghausAlgorithm instance."""
    from powermem.intelligence.ebbinghaus_algorithm import EbbinghausAlgorithm
    return EbbinghausAlgorithm(ebbinghaus_config)


@pytest.fixture
def sample_memory_working():
    """Working memory sample for Ebbinghaus tests."""
    return {
        "metadata": {
            "intelligence": {
                "current_retention": 0.9,
                "memory_type": "working",
                "access_count": 0,
                "last_reviewed": datetime.now(timezone.utc).isoformat(),
                "initial_retention": 1.0,
                "decay_rate": 1.5,
            }
        },
        "created_at": (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
    }


@pytest.fixture
def sample_memory_long_term():
    """Long-term memory sample for Ebbinghaus tests."""
    return {
        "metadata": {
            "intelligence": {
                "current_retention": 0.9,
                "memory_type": "long_term",
                "access_count": 0,
                "last_reviewed": datetime.now(timezone.utc).isoformat(),
                "initial_retention": 1.0,
                "decay_rate": 1.5,
            }
        },
        "created_at": (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
    }


@pytest.fixture
def sample_memory_with_access():
    """Memory with multiple accesses (reinforced)."""
    return {
        "metadata": {
            "intelligence": {
                "current_retention": 0.95,
                "memory_type": "working",
                "access_count": 5,
                "last_reviewed": datetime.now(timezone.utc).isoformat(),
                "initial_retention": 1.0,
                "decay_rate": 1.5,
            }
        },
        "created_at": (datetime.now(timezone.utc) - timedelta(hours=10)).isoformat(),
    }
