# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import annotations

import argparse
import json
import os
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

import pytest

from tests.e2e.topic_memory_product import harness
from tests.e2e.topic_memory_product.common import (
    ArtifactIdentity,
    FakeInference,
    ProductChainError,
    require_no_worker_failures,
    run_e0,
    start_loopback_server,
)


def test_r8_e0_runs_the_complete_hermetic_topic_product_chain(tmp_path: Path) -> None:
    report = run_e0(tmp_path / "r8-e0")

    assert report["status"] == "PASS"
    assert report["worker_failures"] == []
    chain = cast(dict[str, Any], report["chain"])
    assert isinstance(chain, dict)
    assert chain["flush"]["returned_before_generation_completed"] is True
    assert chain["search"]["mode"] == "hybrid"
    assert chain["prepared_context"]["full_detail_absent"] is True
    assert chain["mcp"]["tools"] == ["search_topic_memory", "get_topic_memory"]
    cleanup = cast(dict[str, Any], report["cleanup"])
    assert cleanup == {
        "powercontext_port_closed": True,
        "fake_provider_port_closed": True,
        "temporary_runtime_removed": True,
    }
    assert not any(path.name.startswith(".runtime-") for path in (tmp_path / "r8-e0").iterdir())


def _completed_mcp_event(
    sequence: int,
    *,
    tool: str,
    arguments: dict[str, object],
    result: object,
) -> dict[str, object]:
    return {
        "type": "item.completed",
        "item": {
            "id": f"item_{sequence}",
            "type": "mcp_tool_call",
            "server": "powercontext",
            "tool": tool,
            "arguments": arguments,
            "result": result,
            "status": "completed",
        },
    }


def _codex_search_get_events(
    *,
    search_ref: ArtifactIdentity,
    get_ref: ArtifactIdentity,
    get_scope_id: str = harness.E1_SCOPE_ID,
) -> list[dict[str, object]]:
    search_result = {"content": [{"type": "text", "text": json.dumps({"hits": [{"artifact": search_ref.as_dict()}]})}]}
    return [
        _completed_mcp_event(
            1,
            tool="search_topic_memory",
            arguments={"scope_id": harness.E1_SCOPE_ID, "query": harness.E1_CANARY, "limit": 8},
            result=search_result,
        ),
        _completed_mcp_event(
            2,
            tool="get_topic_memory",
            arguments={"scope_id": get_scope_id, "artifact": get_ref.as_dict()},
            result={"content": []},
        ),
    ]


def test_codex_search_get_binding_requires_adjacent_calls_with_same_full_ref_and_scope() -> None:
    exact_ref = ArtifactIdentity(family="topic", artifact_id="topic-r8", revision=7)
    evidence = harness._validate_codex_search_get_binding(
        _codex_search_get_events(search_ref=exact_ref, get_ref=exact_ref),
        scope_id=harness.E1_SCOPE_ID,
        query=harness.E1_CANARY,
        exact_ref=exact_ref,
    )

    assert evidence["tools"] == ["search_topic_memory", "get_topic_memory"]
    assert evidence["adjacent_completed_mcp_calls"] is True
    assert evidence["same_scope"] is True
    assert evidence["search_result_exact_ref"] == exact_ref.as_dict()
    assert evidence["get_argument_exact_ref"] == exact_ref.as_dict()


def test_codex_search_get_binding_rejects_correct_search_with_wrong_get_ref() -> None:
    exact_ref = ArtifactIdentity(family="topic", artifact_id="topic-r8", revision=7)
    wrong_ref = ArtifactIdentity(family="topic", artifact_id="topic-r8-other", revision=7)

    with pytest.raises(ProductChainError, match="complete expected ArtifactRef"):
        harness._validate_codex_search_get_binding(
            _codex_search_get_events(search_ref=exact_ref, get_ref=wrong_ref),
            scope_id=harness.E1_SCOPE_ID,
            query=harness.E1_CANARY,
            exact_ref=exact_ref,
        )


def test_codex_search_get_binding_rejects_correct_id_with_wrong_get_revision() -> None:
    exact_ref = ArtifactIdentity(family="topic", artifact_id="topic-r8", revision=7)
    wrong_ref = ArtifactIdentity(family="topic", artifact_id="topic-r8", revision=8)

    with pytest.raises(ProductChainError, match="complete expected ArtifactRef"):
        harness._validate_codex_search_get_binding(
            _codex_search_get_events(search_ref=exact_ref, get_ref=wrong_ref),
            scope_id=harness.E1_SCOPE_ID,
            query=harness.E1_CANARY,
            exact_ref=exact_ref,
        )


def test_codex_search_get_binding_rejects_different_get_scope() -> None:
    exact_ref = ArtifactIdentity(family="topic", artifact_id="topic-r8", revision=7)

    with pytest.raises(ProductChainError, match="different scope_id"):
        harness._validate_codex_search_get_binding(
            _codex_search_get_events(
                search_ref=exact_ref,
                get_ref=exact_ref,
                get_scope_id="project:wrong-scope",
            ),
            scope_id=harness.E1_SCOPE_ID,
            query=harness.E1_CANARY,
            exact_ref=exact_ref,
        )


def test_worker_failure_capture_cannot_be_reported_as_pass() -> None:
    failures: list[dict[str, object]] = [{"stage": "topic-memory", "error_code": "generation_failed"}]

    with pytest.raises(ProductChainError, match="E1 captured background-worker failures"):
        require_no_worker_failures("E1", failures)


def test_e1_codex_generation_and_plugin_subprocesses_exclude_layer_secrets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    embedding_secret = "r8-embedding-authorization-secret"  # noqa: S105 - synthetic canary.
    oceanbase_secret = "r8-oceanbase-password-secret"  # noqa: S105 - synthetic canary.
    source_environment = {
        "PATH": os.environ["PATH"],
        "HOME": str(tmp_path / "home"),
        "POWERCONTEXT_R8_EMBEDDING_HEADERS_JSON": json.dumps({"Authorization": embedding_secret}),
        "POWERCONTEXT_R8_OCEANBASE_URL": f"mysql+aoceanbase://r8:{oceanbase_secret}@db.invalid/r8",
        "UNRELATED_SECRET": "r8-unrelated-secret",
    }
    environment = harness._e1_subprocess_environment(source_environment)
    observed_environments: list[dict[str, str]] = []
    installed = tmp_path / "installed-plugin"
    manifest = installed / ".codex-plugin" / "plugin.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text('{"name":"powercontext","version":"test"}\n', encoding="utf-8")

    def fake_run(
        command: tuple[str, ...],
        *,
        cwd: Path,
        env: dict[str, str] | Mapping[str, str],
        timeout: float,
        input_data: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        del cwd, timeout, input_data
        observed_environments.append(dict(env))
        if "--output-last-message" in command:
            output_index = command.index("--output-last-message") + 1
            Path(command[output_index]).write_text("{}\n", encoding="utf-8")
        if command[1:4] == ("plugin", "marketplace", "add"):
            stdout = '{"name":"powercontext"}\n'
        elif command[1:3] == ("plugin", "add"):
            stdout = json.dumps({"installedPath": str(installed)})
        else:
            stdout = '{"type":"turn.completed"}\n'
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(harness, "_run", fake_run)
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    harness._run_codex(
        codex_home=codex_home,
        fixture=fixture,
        environment=environment,
        prompt="synthetic",
        model="test-codex",
        timeout=1,
    )
    bridge = harness._RealCodexChatBridge(
        codex_home=codex_home,
        fixture=fixture,
        environment=environment,
        model="test-generation",
        timeout=1,
    )
    bridge._generate(
        {"response_format": {"json_schema": {"schema": {"type": "object"}}}},
        1,
    )
    harness._install_current_plugin(codex_home=codex_home, environment=environment, timeout=1)
    for observed in observed_environments:
        encoded = json.dumps(observed, sort_keys=True)
        assert "POWERCONTEXT_R8_EMBEDDING_HEADERS_JSON" not in observed
        assert "POWERCONTEXT_R8_OCEANBASE_URL" not in observed
        assert "UNRELATED_SECRET" not in observed
        assert embedding_secret not in encoded
        assert oceanbase_secret not in encoded
        assert observed["OPENAI_API_KEY"] == "r8-loopback-only"


def test_environment_layers_dispatch_every_fully_configured_real_layer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, Path, object]] = []

    def e2(directory: Path, **kwargs: object) -> dict[str, object]:
        calls.append(("E2", directory, kwargs["config"]))
        assert kwargs["e1_status"] == "PASS"
        return {"status": "PASS"}

    def e3(directory: Path, **kwargs: object) -> dict[str, object]:
        calls.append(("E3", directory, kwargs["config"]))
        return {"status": "PASS"}

    def e4(directory: Path, **kwargs: object) -> dict[str, object]:
        calls.append(("E4", directory, kwargs["generation_timeout"]))
        return {"status": "PASS"}

    monkeypatch.setattr(harness, "run_e2", e2)
    monkeypatch.setattr(harness, "run_e3", e3)
    monkeypatch.setattr(harness, "run_e4", e4)
    monkeypatch.setattr(harness.importlib.util, "find_spec", lambda _name: object())
    environment = {
        "POWERCONTEXT_R8_EMBEDDING_MODEL": "openai:test-embedding",
        "POWERCONTEXT_R8_EMBEDDING_PROFILE_ID": "r8-real-embedding-8-unit",
        "POWERCONTEXT_R8_EMBEDDING_DIMENSION": "8",
        "POWERCONTEXT_R8_EMBEDDING_BASE_URL": "https://embedding.example.invalid/v1",
        "POWERCONTEXT_R8_EMBEDDING_HEADERS_JSON": '{"Authorization":"secret-value"}',
        "POWERCONTEXT_R8_OCEANBASE_URL": (
            "mysql+aoceanbase://r8:secret@db.example.invalid:2881/r8_disposable?charset=utf8mb4"
        ),
        "POWERCONTEXT_R8_SEEKDB_ENABLED": "1",
    }

    layers = harness._execute_environment_layers(
        requested={"e0", "e1", "e2", "e3", "e4"},
        directory=tmp_path,
        e1_status="PASS",
        generation_timeout=42,
        environment=environment,
    )

    assert {name: layer["status"] for name, layer in layers.items()} == {
        "E2": "PASS",
        "E3": "PASS",
        "E4": "PASS",
    }
    assert [name for name, _directory, _config in calls] == ["E2", "E3", "E4"]
    embedding = cast(Any, calls[0][2])
    assert (embedding.model, embedding.profile_id, embedding.dimension) == (
        "openai:test-embedding",
        "r8-real-embedding-8-unit",
        8,
    )
    assert embedding.headers["Authorization"].get_secret_value() == "secret-value"
    oceanbase = cast(Any, calls[1][2])
    assert oceanbase.schema_fingerprint == harness.digest_text("r8_disposable")


def test_missing_or_unrequested_layers_never_dispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise AssertionError

    monkeypatch.setattr(harness, "run_e2", unexpected)
    monkeypatch.setattr(harness, "run_e3", unexpected)
    monkeypatch.setattr(harness, "run_e4", unexpected)

    missing = harness._execute_environment_layers(
        requested={"e0", "e2", "e3", "e4"},
        directory=tmp_path,
        e1_status="PASS",
        generation_timeout=42,
        environment={"POWERCONTEXT_R8_EMBEDDING_MODEL": "openai:test"},
    )
    assert missing["E2"]["status"] == "UNAVAILABLE"
    assert "POWERCONTEXT_R8_EMBEDDING_PROFILE_ID" in str(missing["E2"]["gap"])
    assert "POWERCONTEXT_R8_EMBEDDING_DIMENSION" in str(missing["E2"]["gap"])
    assert missing["E3"]["gap"] == ("missing dedicated disposable OceanBase URL/schema: POWERCONTEXT_R8_OCEANBASE_URL")
    assert missing["E4"]["gap"] == "missing explicit opt-in: POWERCONTEXT_R8_SEEKDB_ENABLED=1"

    unrequested = harness._execute_environment_layers(
        requested={"e0"},
        directory=tmp_path,
        e1_status="UNAVAILABLE",
        generation_timeout=42,
        environment={
            "POWERCONTEXT_R8_EMBEDDING_MODEL": "configured-but-unrequested",
            "POWERCONTEXT_R8_OCEANBASE_URL": "configured-but-unrequested",
            "POWERCONTEXT_R8_SEEKDB_ENABLED": "1",
        },
    )
    assert {name: layer["gap"] for name, layer in unrequested.items()} == {
        "E2": "not requested in this invocation",
        "E3": "not requested in this invocation",
        "E4": "not requested in this invocation",
    }


def test_e2_automatically_requests_its_e1_prerequisite() -> None:
    assert harness._requested_layers("e2") == {"e0", "e1", "e2"}


def test_e1_unavailable_makes_configured_e2_unavailable_without_dispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = argparse.Namespace(
        layers="e2",
        output=tmp_path / "e1-unavailable",
        codex_timeout=10,
        generation_timeout=10,
        codex_model="test-codex",
        generation_model="test-generation",
    )
    monkeypatch.setattr(harness, "_parse_args", lambda: args)
    monkeypatch.setattr(harness, "_version", lambda *_args, **_kwargs: "test-head")
    monkeypatch.setattr(harness, "run_e0", lambda _directory: {"status": "PASS"})
    monkeypatch.setattr(harness, "run_e1", lambda _directory, **_kwargs: {"status": "UNAVAILABLE"})
    monkeypatch.setattr(harness, "run_e2", lambda *_args, **_kwargs: pytest.fail("E2 runner was called"))
    configured = {
        "POWERCONTEXT_R8_EMBEDDING_MODEL": "openai:test-embedding",
        "POWERCONTEXT_R8_EMBEDDING_PROFILE_ID": "r8-real-embedding-8-unit",
        "POWERCONTEXT_R8_EMBEDDING_DIMENSION": "8",
    }

    with patch.dict(os.environ, configured, clear=False):
        assert harness.main() == 0

    report = json.loads((args.output / "r8-report.json").read_text(encoding="utf-8"))
    assert report["status"] == "PARTIAL"
    assert report["layers"]["E1"]["status"] == "UNAVAILABLE"
    assert report["layers"]["E2"] == {
        "schema": "powercontext.topic-memory-r8.e2.v1",
        "status": "UNAVAILABLE",
        "gap": "E1 prerequisite is UNAVAILABLE; E2 runner was not executed",
        "safe_checks_completed": [
            "E1 is automatically requested with E2",
            "the embedding provider was not contacted without an E1 PASS",
        ],
        "passed": False,
    }


def test_e1_failure_keeps_configured_e2_run_failed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = argparse.Namespace(
        layers="e2",
        output=tmp_path / "e1-fail",
        codex_timeout=10,
        generation_timeout=10,
        codex_model="test-codex",
        generation_model="test-generation",
    )
    monkeypatch.setattr(harness, "_parse_args", lambda: args)
    monkeypatch.setattr(harness, "_version", lambda *_args, **_kwargs: "test-head")
    monkeypatch.setattr(harness, "run_e0", lambda _directory: {"status": "PASS"})
    monkeypatch.setattr(harness, "run_e1", lambda _directory, **_kwargs: {"status": "FAIL"})
    monkeypatch.setattr(harness, "run_e2", lambda *_args, **_kwargs: pytest.fail("E2 runner was called"))

    with pytest.raises(ProductChainError, match="E2 requires E1 PASS; observed FAIL"):
        harness.main()

    report = json.loads((args.output / "r8-report.json").read_text(encoding="utf-8"))
    assert report["status"] == "FAIL"
    assert report["failure"]["type"] == "ProductChainError"


def test_e2_configured_provider_runs_hybrid_and_controlled_fallback(
    tmp_path: Path,
) -> None:
    provider = start_loopback_server(FakeInference().app())
    try:
        config, unavailable = harness._real_embedding_config({
            "POWERCONTEXT_R8_EMBEDDING_MODEL": "openai:r8-configured-embedding",
            "POWERCONTEXT_R8_EMBEDDING_PROFILE_ID": "r8-configured-embedding-4-unit",
            "POWERCONTEXT_R8_EMBEDDING_DIMENSION": "4",
            "POWERCONTEXT_R8_EMBEDDING_BASE_URL": f"{provider.base_url}/v1",
            "POWERCONTEXT_R8_EMBEDDING_HEADERS_JSON": '{"Authorization":"Bearer r8-test"}',
        })
        assert unavailable is None
        assert config is not None

        report = harness.run_e2(
            tmp_path / "r8-e2",
            config=config,
            e1_status="PASS",
            generation_timeout=30,
        )
    finally:
        provider.stop()

    assert report["status"] == "PASS"
    hybrid = cast(dict[str, Any], report["hybrid_chain"])
    assert hybrid["search"]["mode"] == "hybrid"
    fallback = cast(dict[str, Any], report["controlled_fallback"])
    assert fallback["search_mode"] == "fts"
    assert fallback["exact_ref"] == hybrid["search"]["exact_ref"]
    assert fallback["signal"] == {
        "event": "topic_memory.search.embedding_fallback",
        "mode": "fts",
        "error_code": "inference_unavailable",
    }


def test_main_can_report_all_requested_layers_pass(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = argparse.Namespace(
        layers="e0,e1,e2,e3,e4",
        output=tmp_path / "all-pass",
        codex_timeout=10,
        generation_timeout=10,
        codex_model="test-codex",
        generation_model="test-generation",
    )
    monkeypatch.setattr(harness, "_parse_args", lambda: args)
    monkeypatch.setattr(harness, "_version", lambda *_args, **_kwargs: "test-head")
    monkeypatch.setattr(harness, "run_e0", lambda _directory: {"status": "PASS"})
    monkeypatch.setattr(harness, "run_e1", lambda _directory, **_kwargs: {"status": "PASS"})
    monkeypatch.setattr(
        harness,
        "_execute_environment_layers",
        lambda **_kwargs: {
            "E2": {"status": "PASS"},
            "E3": {"status": "PASS"},
            "E4": {"status": "PASS"},
        },
    )

    assert harness.main() == 0
    report = json.loads((args.output / "r8-report.json").read_text(encoding="utf-8"))
    assert report["status"] == "PASS"


def test_e3_background_environment_loads_the_production_split_role() -> None:
    config, unavailable = harness._oceanbase_layer_config({
        "POWERCONTEXT_R8_OCEANBASE_URL": (
            "mysql+aoceanbase://r8:secret@db.example.invalid:2881/r8_disposable?charset=utf8mb4"
        )
    })
    assert unavailable is None
    assert config is not None
    environment = harness._background_environment(config, inference_base_url="http://127.0.0.1:12345")

    with patch.dict(os.environ, environment, clear=True):
        settings = harness.ServerSettings()

    assert settings.runtime.artifact_processing_role == "background"
    assert settings.database.kind == "oceanbase"
    assert settings.inference.generation_model == "openai-chat:r8-fake-generation"
    assert str(settings.inference.generation_base_url) == "http://127.0.0.1:12345/v1"
    assert settings.handoff_report.enabled is False
