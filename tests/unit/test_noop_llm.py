from types import SimpleNamespace

from powermem.agent.components.scope_controller import ScopeController
from powermem.agent.types import MemoryScope
import powermem.config_loader as config_loader
import powermem.settings as settings
from powermem.intelligence.importance_evaluator import ImportanceEvaluator
from powermem.intelligence.memory_optimizer import MemoryOptimizer
from powermem.intelligence.skill_manager import SkillManager
from powermem.integrations.llm.factory import LLMFactory
from powermem.user_memory.query_rewrite.rewriter import QueryRewriter


class _NoopRaisingLLM:
    is_noop = True

    def generate_response(self, *args, **kwargs):
        raise AssertionError("noop LLM should not be called")


class _FailingStorage:
    def get_all_memories(self, *args, **kwargs):
        raise AssertionError("storage should not be read when compression is skipped")


def test_noop_llm_factory_returns_disabled_provider():
    llm = LLMFactory.create("noop", {})

    assert getattr(llm, "is_noop", False) is True
    assert llm.generate_response(messages=[{"role": "user", "content": "hello"}]) == ""
    assert llm.generate_response(
        messages=[{"role": "user", "content": "hello"}],
        tools=[{"type": "function", "function": {"name": "extract", "parameters": {}}}],
    ) == {"content": "", "tool_calls": []}


def test_load_config_from_env_supports_noop_llm(monkeypatch):
    monkeypatch.setattr(config_loader, "_DEFAULT_ENV_FILE", None, raising=False)
    monkeypatch.setattr(settings, "_DEFAULT_ENV_FILE", None, raising=False)
    new_config = dict(config_loader.LLMSettings.model_config)
    new_config["env_file"] = None
    monkeypatch.setattr(config_loader.LLMSettings, "model_config", new_config)
    monkeypatch.setenv("LLM_PROVIDER", "noop")
    monkeypatch.delenv("LLM_MODEL", raising=False)

    config = config_loader.load_config_from_env()

    assert config["llm"]["provider"] == "noop"
    assert config["llm"]["config"]["model"] == "noop"


def test_noop_llm_skips_skill_distillation_and_merge():
    manager = SkillManager(_NoopRaisingLLM())

    assert (
        manager.distill(
            [{"role": "user", "content": "Remember this flow"}],
            "2026-06-14",
        )
        == []
    )
    assert manager.merge("existing skill", "new skill") == {"action": "skip"}


def test_noop_llm_uses_rule_based_importance_evaluation():
    evaluator = ImportanceEvaluator({}, {})
    evaluator.set_llm(_NoopRaisingLLM())

    score = evaluator.evaluate_importance("Important preference: I love Python")

    assert score > 0


def test_noop_llm_skips_query_rewrite():
    rewriter = QueryRewriter(_NoopRaisingLLM(), {"enabled": True})

    result = rewriter.rewrite(
        "coffee preference",
        profile_content="The user likes black coffee.",
    )

    assert result.original_query == "coffee preference"
    assert result.rewritten_query == "coffee preference"
    assert result.is_rewritten is False
    assert result.metadata == {"skipped_reason": "llm_disabled"}


def test_noop_llm_skips_memory_compression():
    optimizer = MemoryOptimizer(_FailingStorage(), _NoopRaisingLLM())

    assert optimizer.compress(user_id="user-noop") == {
        "total_processed": 0,
        "clusters_found": 0,
        "compressed_count": 0,
        "new_memories_created": 0,
        "errors": 0,
    }


def test_noop_llm_scope_controller_uses_default_scope():
    config = SimpleNamespace(
        llm=SimpleNamespace(provider="noop", config={}),
        agent_memory=SimpleNamespace(
            multi_agent_config=SimpleNamespace(default_scope="public")
        ),
    )
    controller = ScopeController(config)
    controller.llm = _NoopRaisingLLM()

    assert (
        controller.determine_scope(
            agent_id="agent-noop",
            metadata={"content": "This would normally need scope analysis."},
        )
        is MemoryScope.PUBLIC
    )
