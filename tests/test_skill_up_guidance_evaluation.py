from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

from powercontext.server.mcp import _MCP_OPERATION_IDS

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "evaluation/skill-up"
EVAL = PROJECT / "evals/eval.yaml"
CASE_NAMES = (
    "01-ordinary-coding.yaml",
    "02-explicit-memory-save.yaml",
    "03-empty-memory-search.yaml",
    "04-inspect-candidates.yaml",
    "05-failed-memory-save.yaml",
)
PREFIX = "mcp__powercontext__"


def load_yaml(path: Path) -> dict[str, Any]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def judge_rules(case: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    judge = case["judge"]
    assert isinstance(judge, dict) and judge["type"] == "rule_based"
    rules = judge.get(kind, [])
    assert isinstance(rules, list)
    return rules


def success_rules(case: dict[str, Any]) -> list[dict[str, Any]]:
    return judge_rules(case, "success")


def tool_names(case: dict[str, Any], rule_name: str, *, kind: str = "success") -> set[str]:
    names: set[str] = set()
    for rule in judge_rules(case, kind):
        payload = rule.get(rule_name)
        if isinstance(payload, dict):
            name = payload.get("name")
            assert isinstance(name, str)
            names.add(name)
    return names


def output_matches(rule: dict[str, list[str]], text: str) -> bool:
    return (
        all(re.search(pattern, text) for pattern in rule.get("all", []))
        and (not rule.get("any") or any(re.search(pattern, text) for pattern in rule["any"]))
        and not any(re.search(pattern, text) for pattern in rule.get("not", []))
    )


def test_eval_uses_real_mcp_serial_benchmark_and_machine_reports() -> None:
    config = load_yaml(EVAL)
    assert config["schema_version"] == "v1alpha1"
    assert config["environment"] == {"type": "none"}
    assert config["engine"] == {"name": "claude_code"}
    assert config["judge"] == {"type": "rule_based"}
    assert config["benchmark"] == {"enabled": True}
    assert config["report"] == {"formats": ["json", "junit", "html"], "artifacts": ["transcript"]}
    assert config["skills"] == [
        {
            "source": "local_path",
            "path": "vendor/powercontext-plugin",
            "include": ["SKILL.md", "references/**", "scripts/**", "*.py"],
        }
    ]

    cases = config["cases"]
    assert isinstance(cases, dict)
    assert cases["defaults"] == {"timeout_seconds": 300, "max_turns": 8}
    assert cases["parallelism"] == 1
    assert cases["files"] == [f"evals/cases/{name}" for name in CASE_NAMES]

    servers = config["mcp"]["servers"]
    assert servers == [
        {
            "name": "powercontext",
            "mode": "real",
            "transport": "http",
            "endpoint": "http://127.0.0.1:8000/mcp",
            "config_ref": "evals/fixtures/mcp/powercontext.yaml",
        }
    ]
    assert "headers" not in servers[0]


def test_cases_cover_positive_negative_and_authorization_controls() -> None:
    cases = {name: load_yaml(PROJECT / "evals/cases" / name) for name in CASE_NAMES}
    for case in cases.values():
        for kind in ("success", "failure"):
            assert not tool_names(case, "tool_called_in_turn", kind=kind)
            assert not tool_names(case, "tool_not_called_in_turn", kind=kind)

    ordinary = cases["01-ordinary-coding.yaml"]
    forbidden = tool_names(ordinary, "tool_called", kind="failure")
    assert forbidden == {PREFIX + name for name in _MCP_OPERATION_IDS}
    assert success_rules(ordinary) == []

    explicit_save = cases["02-explicit-memory-save.yaml"]
    assert success_rules(explicit_save) == [{"tool_called": {"name": PREFIX + "remember_memory"}}]

    empty_search = cases["03-empty-memory-search.yaml"]
    assert success_rules(empty_search) == [
        {"tool_called": {"name": PREFIX + "search_memory"}},
        {"output_matches": {"not": ["(?i)(restored|recalled|persisted|saved) (the |this )?(context|memory)"]}},
    ]
    assert tool_names(empty_search, "tool_called", kind="failure") == {
        PREFIX + "remember_memory",
        PREFIX + "list_memory_entries",
    }

    inspect = cases["04-inspect-candidates.yaml"]
    assert success_rules(inspect) == [{"tool_called": {"name": PREFIX + "list_artifact_candidates"}}]
    assert tool_names(inspect, "tool_called", kind="failure") == {
        PREFIX + "approve_artifact_candidate",
        PREFIX + "reject_artifact_candidate",
        PREFIX + "revise_artifact_candidate",
        PREFIX + "publish_artifact",
    }

    failed = cases["05-failed-memory-save.yaml"]
    assert success_rules(failed) == [
        {"tool_called": {"name": PREFIX + "remember_memory"}},
        {
            "output_matches": {
                "all": ["(?i)^(the )?PowerContext Memory write failed:"],
                "not": [
                    "(?i)Claude Code('s)? local memory",
                    "(?im)^\\s*(saved|persisted|remembered|recorded)\\b",
                    "(?i)\\b(is|was|has been)\\s+(saved|persisted|remembered|recorded)\\b",
                    "(?is)\\b(success|successful|successfully|completed)\\b.{0,120}\\b(saved|persisted|remembered|recorded)\\b",
                    "(?is)\\b(saved|persisted|remembered|recorded)\\b.{0,40}\\b(success|successful|successfully)\\b",
                ],
            }
        },
    ]
    assert failed["mcp"] == {
        "servers": [
            {
                "name": "powercontext",
                "mode": "mocked",
                "config_ref": "evals/fixtures/mcp/failed-write.yaml",
            }
        ]
    }


def test_mcp_fixtures_keep_auth_and_failure_at_the_supported_boundary() -> None:
    real = load_yaml(PROJECT / "evals/fixtures/mcp/powercontext.yaml")
    assert real == {"headers": {"Authorization": "${POWERCONTEXT_CLAUDE_AUTHORIZATION}"}}
    failed = load_yaml(PROJECT / "evals/fixtures/mcp/failed-write.yaml")
    assert set(failed["tool_responses"]) == {"remember_memory"}
    assert failed["tool_responses"]["remember_memory"]["default"]["status"] == "failed"


def test_failed_write_output_rule_rejects_false_success_and_local_fallback() -> None:
    case = load_yaml(PROJECT / "evals/cases/05-failed-memory-save.yaml")
    rule = success_rules(case)[1]["output_matches"]

    assert output_matches(
        rule,
        "PowerContext Memory write failed: the fixture rejected it, so no PowerContext entry was created.",
    )
    assert not output_matches(rule, "The PowerContext memory was saved. A local cleanup step failed.")
    assert not output_matches(
        rule,
        "PowerContext Memory write failed: I saved the constraint in Claude Code's local memory instead.",
    )
    assert not output_matches(rule, "Saved. A cleanup step failed.")


def test_vendored_skill_is_locked_and_reproducible() -> None:
    lock = json.loads((PROJECT / "skill.lock.json").read_text(encoding="utf-8"))
    assert lock["schema"] == "powercontext.skill-up-skill-lock.v1"
    assert len(lock["source_revision"]) == 40
    assert set(lock["files"]) == {
        "SKILL.md",
        "claude_code_settings.py",
        "powercontext_client_config.py",
        "references/review-publication.md",
        "references/scope-memory.md",
        "references/work-handoff.md",
        "scope_binding_errors.py",
        "scripts/workspace_scope.py",
    }
    assert all(len(item["sha256"]) == 64 for item in lock["files"].values())
    completed = subprocess.run(
        [str(PROJECT / "sync-skill.sh"), "--check"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    resolver_help = subprocess.run(
        [sys.executable, str(PROJECT / "vendor/powercontext-plugin/scripts/workspace_scope.py"), "--help"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert resolver_help.returncode == 0, resolver_help.stderr


def test_documentation_exposes_exact_commands_and_limits_claims() -> None:
    readme = (PROJECT / "README.md").read_text(encoding="utf-8")
    required = (
        'test "$(skill-up --version)" = "skill-up version 0.12.0"',
        'export PATH="$(pwd -P)/.venv/bin:$PATH"',
        "test \"$(python3 -c 'import sys; print(sys.version_info >= (3, 11))')\" = True",
        "skill-up validate evaluation/skill-up/evals/eval.yaml",
        "skill-up run evaluation/skill-up/evals/eval.yaml --baseline",
        "curl --fail --silent --show-error http://127.0.0.1:8000/health/ready",
        "export POWERCONTEXT_SKILL_UP_TOKEN=skill-up-fixture-token",
        'export POWERCONTEXT_SERVER_AUTH_TOKEN="$POWERCONTEXT_SKILL_UP_TOKEN"',
        'export POWERCONTEXT_CLAUDE_AUTHORIZATION="Bearer $POWERCONTEXT_SKILL_UP_TOKEN"',
        "POWERCONTEXT_CLAUDE_AUTHORIZATION",
        "CLAUDE_PLUGIN_ROOT",
        "disableAllHooks",
        "bypassPermissions",
        "bounded recall",
        "automatic Capture/Flush",
        "Memory quality",
        "Claude Code + MCP",
    )
    for text in required:
        assert text in readme

    for path in (
        ROOT / "evaluation/README.md",
        ROOT / "docs/en/development/integration-guidance-evaluation.md",
        ROOT / "docs/zh/development/integration-guidance-evaluation.md",
    ):
        content = path.read_text(encoding="utf-8")
        assert "evaluation/skill-up" in content or "../../../evaluation/skill-up" in content
