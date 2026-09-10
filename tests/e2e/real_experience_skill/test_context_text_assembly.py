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

"""Opt-in configured database/LLM and native Codex context assembly acceptance."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
import uuid
from pathlib import Path

import pytest
from dotenv import load_dotenv
from pydantic import SecretStr
from sqlalchemy import bindparam, inspect, text
from sqlalchemy.engine import make_url

from powercontext.builtin.persistence.oceanbase import OceanBaseConfig, OceanBaseProfile
from powercontext.builtin.persistence.seekdb import SeekDBConfig, SeekDBProfile
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.client import PowerContextClient
from powercontext.http import (
    ApproveArtifactCandidateRequest,
    CaptureContentSourceRequest,
    CreateScopeRequest,
    GenerateExperienceRequest,
    GetExperienceRequest,
    GetMemoryEntryRequest,
    MemorySearchMode,
    PrepareContextRequest,
    RememberMemoryRequest,
    ReviseMemoryEntryRequest,
    SearchMemoryRequest,
    UpdateScopeRequest,
)
from powercontext.server.settings import McpConfig, MetricsConfig, ServerSettings

from .harness import _configured_access_token, _start_configured_server, _without_scheduled_processing

pytestmark = pytest.mark.real_e2e
PROJECT_ROOT = Path(__file__).resolve().parents[3]
ASSEMBLY = {
    "sections": [{"family": "experience", "limit": 2}, {"family": "memory", "limit": 3}],
    "show": ["confidence", "recall_rank"],
}
QUERY = "How should the deployment configuration and release gate be repaired and verified?"
CHECK = 'import json\nfrom pathlib import Path\nassert json.loads(Path("config.json").read_text())["mode"] == "strict"\nprint("release gate passed")\n'


def test_configured_services_and_native_codex_consume_standard_text(tmp_path, pytestconfig):
    load_dotenv(pytestconfig.getoption("real_e2e_env_file"), override=False)
    configured = ServerSettings()
    assert configured.inference.generation_model and configured.inference.embedding_model
    settings = _without_scheduled_processing(configured).model_copy(
        update={
            "metrics": MetricsConfig(enabled=False),
            "mcp": McpConfig(enabled=True),
            "runtime": _without_scheduled_processing(configured).runtime.model_copy(
                update={"memory_rerank_enabled": False}
            ),
        }
    )
    if isinstance(settings.database, SQLiteConfig):
        settings = settings.model_copy(
            update={"database": SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'real.db'}")}
        )
    token = _configured_access_token(settings)
    report = {"backend": type(settings.database).__name__.removesuffix("Config").lower(), "checks": [], "cleanup": {}}
    scope_ids = []
    server = None
    temporary_database = None
    real_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    original_state = _user_state(real_home)
    try:
        if isinstance(settings.database, OceanBaseConfig):
            database_name = f"pc_assembly_{uuid.uuid4().hex}"
            asyncio.run(_create_database(settings.database, database_name))
            temporary_database = database_name
            test_url = make_url(settings.database.url.get_secret_value()).set(database=temporary_database)
            settings = settings.model_copy(
                update={
                    "database": settings.database.model_copy(
                        update={
                            "url": SecretStr(test_url.render_as_string(hide_password=False)),
                        }
                    )
                }
            )
            report["temporary_database"] = temporary_database
            (tmp_path / "assembly-report.json").write_text(json.dumps(report, indent=2))
        print("Starting isolated configured-service context assembly acceptance", flush=True)
        server = _start_configured_server(settings)
        scope_id, nonce, entry_version = asyncio.run(_api_scenario(server.base_url, token, scope_ids, report, tmp_path))
        with tempfile.TemporaryDirectory(prefix="powercontext-assembly-host-") as temp:
            root = Path(temp)
            home = _install_codex_plugin(root, real_home, server.base_url, token)
            repository = root / "repository"
            repository.mkdir()
            (repository / "config.json").write_text('{"mode": "permissive"}\n')
            (repository / "test_config.py").write_text(CHECK)
            environment = {
                **os.environ,
                "CODEX_HOME": str(home),
                "NO_COLOR": "1",
                "POWERCONTEXT_CODEX_SCOPE_ID": scope_id,
                "POWERCONTEXT_CODEX_CONTEXT_ASSEMBLY": json.dumps(ASSEMBLY),
                "POWERCONTEXT_CODEX_CAPTURE_PROMPTS": "false",
                "POWERCONTEXT_CODEX_REQUEST_TIMEOUT_SECONDS": "8",
                "POWERCONTEXT_CODEX_HTTP_BUDGET_SECONDS": "9",
            }
            if token:
                environment["POWERCONTEXT_CODEX_AUTHORIZATION"] = f"Bearer {token}"
            timeout = pytestconfig.getoption("real_codex_timeout")
            prompt = (
                "Repair the deployment configuration and verify the release gate. Use the historical context already "
                "supplied by the PowerContext prompt hook as evidence, inspect current files, and keep test_config.py "
                "unchanged. Do not call additional memory/context tools. Return the release nonce, exact Memory entry "
                "version, confidence label, and verification result found in the context and live check. "
                "Use unknown for any missing evidence."
            )
            print("Configured API scenarios passed; running native Codex with standard text", flush=True)
            result = _codex_turn(root, repository, environment, prompt, timeout=timeout, name="enabled")
            assert result["nonce"] == nonce, "Native Codex did not receive the selected Memory evidence"
            assert entry_version in result["entry_version"], "Native Codex lost the exact citation"
            assert "unknown" in result["confidence"].lower(), "The host invented a confidence value"
            assert (repository / "test_config.py").read_text() == CHECK
            check = subprocess.run([sys.executable, "test_config.py"], cwd=repository, capture_output=True, text=True)
            assert check.returncode == 0, "Native Codex did not repair and verify the deployment fixture"
            report["checks"].append("native_codex_hook_recall_citation_and_live_repair")
            report["codex_enabled"] = result
            environment["POWERCONTEXT_CODEX_CONTEXT_ASSEMBLY"] = '{"sections": []}'
            disabled = _codex_turn(
                root,
                repository,
                environment,
                "Without using any tools, report the release nonce and exact Memory entry version in the "
                "historical context supplied to this turn. Use unknown for missing evidence. Do not infer values.",
                timeout=timeout,
                name="disabled",
            )
            assert disabled["nonce"].lower() == "unknown"
            assert disabled["entry_version"].lower() == "unknown"
            report["checks"].append("native_codex_empty_sections_negative_control")
            report["codex_disabled"] = disabled
    finally:
        if server is not None:
            server.stop()
        if temporary_database is not None:
            asyncio.run(_drop_database(configured.database, temporary_database))
            report["cleanup"] = {"temporary_database_removed": True, "scope_count": len(scope_ids), "remaining_rows": 0}
        elif scope_ids:
            report["cleanup"] = asyncio.run(_cleanup(settings.database, scope_ids))
        report["cleanup"]["user_codex_state_unchanged"] = _user_state(real_home) == original_state
        (tmp_path / "assembly-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    assert report["cleanup"]["user_codex_state_unchanged"]
    print(f"Real context assembly passed: {len(report['checks'])} checks; report: {tmp_path / 'assembly-report.json'}")


async def _api_scenario(url, token, scope_ids, report, output):
    nonce = f"release-{uuid.uuid4().hex}"
    async with PowerContextClient(url, token=token, timeout=180) as client:
        for name in ["current", "shared", "unrelated"]:
            scope = await client.create_scope(
                CreateScopeRequest(
                    title=f"Context assembly acceptance {name}",
                    summary="Disposable real-service acceptance evidence",
                    idempotency_key=f"context-assembly-{name}-{uuid.uuid4().hex}",
                )
            )
            scope_ids.append(scope.scope_id)
        current, shared, unrelated = scope_ids
        remembered = await client.remember_memory(
            RememberMemoryRequest(
                scope_id=current,
                kind="release-gate",
                text=f"Deployment repair: set config.json mode to strict, keep test_config.py unchanged, and run python3 test_config.py. Release nonce: {nonce}.",
            )
        )
        assert remembered.entry is not None
        citation = remembered.entry.citation
        await client.remember_memory(
            RememberMemoryRequest(
                scope_id=shared,
                kind="constraint",
                text="Shared deployment constraint: preserve the release gate verification script.",
            )
        )
        await client.remember_memory(
            RememberMemoryRequest(
                scope_id=unrelated,
                kind="fact",
                text="Deployment SECRET_UNRELATED_SCOPE must never enter another Scope's context.",
            )
        )
        descriptor = await client.get_scope(current)
        await client.update_scope(
            current,
            UpdateScopeRequest(
                expected_version=descriptor.version,
                title=descriptor.title,
                summary=descriptor.summary,
                context_references=[shared],
            ),
        )
        vector = await client.search_memory(
            SearchMemoryRequest(scope_id=current, query=QUERY, mode=MemorySearchMode.VECTOR, limit=8)
        )
        assert any(hit.citation.entry_version_id == citation.entry_version_id for hit in vector.hits)
        report["checks"].append("configured_embedding_and_database_vector_recall")
        verified_output = await asyncio.to_thread(_verify_source_fixture)
        print("Configured vector retrieval passed; generating Experience with the real LLM", flush=True)
        evidence = await client.capture_content_source(
            CaptureContentSourceRequest(
                scope_id=current,
                source_id="verified-release-gate",
                content=(
                    "Task: repair deployment configuration. Observation: config.json mode=permissive failed the "
                    "release gate. Verified action: changed mode to strict, kept test_config.py unchanged, and ran "
                    f"python3 test_config.py. Observed stdout: {verified_output}. "
                    "This reusable procedure applies to strict deployment configuration."
                ),
                metadata={"kind": "task-outcome", "validation_status": "passed"},
            )
        )
        generated = await client.generate_experience(
            GenerateExperienceRequest(
                scope_id=current,
                source_refs=[evidence.source],
                artifact_refs=[],
                reason="Extract the reusable, verified deployment repair procedure from this exact task evidence.",
            )
        )
        assert generated.candidate is not None, "Configured LLM did not produce the reusable Experience"
        pending = await client.prepare_context(
            PrepareContextRequest.model_validate({
                "scope_id": current,
                "query": QUERY,
                "assembly": ASSEMBLY,
            })
        )
        assert pending.content and "## Experience" not in pending.content
        approved = await client.approve_artifact_candidate(
            ApproveArtifactCandidateRequest(
                scope_id=current,
                candidate_id=generated.candidate.candidate_id,
                expected_version=generated.candidate.version,
            )
        )
        assert approved.result_artifact is not None
        exact_experience = await client.get_experience(
            GetExperienceRequest(scope_id=current, artifact=approved.result_artifact)
        )
        assert exact_experience.source_refs == [evidence.source]
        report["checks"].append("configured_llm_experience_generation_review_and_exact_lineage")
        grouped = await client.prepare_context(
            PrepareContextRequest.model_validate({
                "scope_id": current,
                "query": QUERY,
                "assembly": ASSEMBLY,
            })
        )
        assert grouped.content
        assert grouped.content.index("## Experience") < grouped.content.index("## Memory")
        assert current in grouped.content and shared in grouped.content
        assert "SECRET_UNRELATED_SCOPE" not in grouped.content
        assert "Confidence: unknown (not assessed)" in grouped.content and "Recall rank: 1" in grouped.content
        assert grouped.content_bytes == len(grouped.content.encode()) <= 8000
        (output / "standard-context.md").write_text(grouped.content)
        report["checks"].append("family_order_cross_scope_citations_metadata_and_isolation")
        memory_only = await client.prepare_context(
            PrepareContextRequest.model_validate({
                "scope_id": current,
                "query": QUERY,
                "assembly": {"sections": [{"family": "memory", "limit": 1}]},
            })
        )
        assert memory_only.content and "## Experience" not in memory_only.content
        assert memory_only.content.count("### Memory ") == 1
        for budget in [512, 1024, 2048]:
            bounded = await client.prepare_context(
                PrepareContextRequest.model_validate({
                    "scope_id": current,
                    "query": QUERY,
                    "max_bytes": budget,
                    "assembly": ASSEMBLY,
                })
            )
            assert bounded.content_bytes == len((bounded.content or "").encode()) <= budget
            if bounded.content:
                assert bounded.content.endswith("END_POWERCONTEXT_PREPARED_TEXT_V1")
        report["checks"].append("selected_family_limit_and_utf8_output_budgets")
        legacy = await client.prepare_context(PrepareContextRequest(scope_id=current, query=QUERY))
        assert legacy.content and '"items":[' in legacy.content
        disabled = await client.prepare_context(
            PrepareContextRequest.model_validate({
                "scope_id": current,
                "query": QUERY,
                "assembly": {"sections": []},
            })
        )
        assert disabled.content is None and disabled.content_bytes == 0
        report["checks"].append("legacy_default_and_explicit_empty_sections")
        revised = await client.revise_memory_entry(
            ReviseMemoryEntryRequest(
                scope_id=current,
                citation=citation,
                kind="release-gate",
                text=remembered.entry.text + " Revalidated for the current release.",
            )
        )
        exact = await client.get_memory_entry(GetMemoryEntryRequest(scope_id=current, citation=citation))
        assert exact.text == remembered.entry.text
        assert revised.entry is not None
        report["checks"].append("historical_memory_version_remains_exact_after_revision")
        descriptor = await client.get_scope(current)
        await client.update_scope(
            current,
            UpdateScopeRequest(
                expected_version=descriptor.version,
                title=descriptor.title,
                summary=descriptor.summary,
                context_references=[],
            ),
        )
        # Warm this isolated Scope's retrieval before the native hook's interactive budget.
        await client.prepare_context(
            PrepareContextRequest.model_validate({"scope_id": current, "query": QUERY, "assembly": ASSEMBLY})
        )
        return current, nonce, revised.entry.citation.entry_version_id


def _verify_source_fixture():
    with tempfile.TemporaryDirectory(prefix="assembly-producer-") as temp:
        root = Path(temp)
        (root / "test_config.py").write_text(CHECK)
        (root / "config.json").write_text('{"mode": "permissive"}')
        before = subprocess.run([sys.executable, "test_config.py"], cwd=root, capture_output=True, text=True)
        assert before.returncode != 0
        (root / "config.json").write_text('{"mode": "strict"}')
        after = subprocess.run([sys.executable, "test_config.py"], cwd=root, capture_output=True, text=True)
        assert after.returncode == 0 and (root / "test_config.py").read_text() == CHECK
        return after.stdout.strip()


def _user_state(home):
    return {
        name: hashlib.sha256((home / name).read_bytes()).hexdigest() if (home / name).exists() else None
        for name in ["auth.json", "config.toml", "AGENTS.md"]
    }


def _toml_value(value):
    if isinstance(value, dict):
        return "{ " + ", ".join(f"{json.dumps(k)} = {_toml_value(v)}" for k, v in value.items() if v is not None) + " }"
    return json.dumps(value)


def _install_codex_plugin(root, real_home, url, token):
    home = root / "codex-home"
    home.mkdir(mode=0o700)
    shutil.copyfile(real_home / "auth.json", home / "auth.json")
    (home / "auth.json").chmod(0o600)
    if (real_home / "config.toml").exists():
        original = tomllib.loads((real_home / "config.toml").read_text())
        selected = {key: original[key] for key in ["model", "model_provider", "model_providers"] if key in original}
        (home / "config.toml").write_text(
            "\n".join(f"{key} = {_toml_value(value)}" for key, value in selected.items()) + "\n"
        )
        (home / "config.toml").chmod(0o600)
    marketplace = root / "marketplace"
    shutil.copytree(
        PROJECT_ROOT / "integrations/codex",
        marketplace,
        ignore=shutil.ignore_patterns(".venv", "__pycache__", ".pytest_cache"),
    )
    mcp_path = marketplace / "plugins/powercontext/.mcp.json"
    mcp = json.loads(mcp_path.read_text())
    mcp["mcpServers"]["powercontext"]["url"] = f"{url}/mcp"
    mcp_path.write_text(json.dumps(mcp))
    environment = {**os.environ, "CODEX_HOME": str(home)}
    if token:
        environment["POWERCONTEXT_CODEX_AUTHORIZATION"] = f"Bearer {token}"
    for args in [
        ["plugin", "marketplace", "add", str(marketplace), "--json"],
        ["plugin", "add", "powercontext@powercontext-local", "--json"],
    ]:
        completed = subprocess.run(
            [str(shutil.which("codex")), *args], env=environment, cwd=root, capture_output=True, text=True, timeout=120
        )
        assert completed.returncode == 0, "Isolated Codex plugin installation failed"
    return home


def _codex_turn(root, repository, environment, prompt, *, timeout, name):
    schema = root / "answer-schema.json"
    schema.write_text(
        json.dumps({
            "type": "object",
            "additionalProperties": False,
            "required": ["nonce", "entry_version", "confidence", "validation"],
            "properties": {key: {"type": "string"} for key in ["nonce", "entry_version", "confidence", "validation"]},
        })
    )
    output = root / f"{name}.json"
    result = subprocess.run(
        [
            str(shutil.which("codex")),
            "-a",
            "never",
            "--disable",
            "memories",
            "--disable",
            "shell_snapshot",
            "exec",
            "--ephemeral",
            "--dangerously-bypass-hook-trust",
            "--json",
            "--skip-git-repo-check",
            "-s",
            "workspace-write",
            "-C",
            str(repository),
            "--output-schema",
            str(schema),
            "-o",
            str(output),
            prompt,
        ],
        env=environment,
        cwd=root,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    assert result.returncode == 0, f"Native Codex {name} scenario failed (exit {result.returncode})"
    return json.loads(output.read_text())


async def _create_database(database, name):
    assert name.startswith("pc_assembly_") and name.removeprefix("pc_assembly_").isalnum()
    async with OceanBaseProfile.open(database, tables=()) as profile, profile.database.transaction() as connection:
        await connection.exec_driver_sql(f"CREATE DATABASE `{name}`")


async def _drop_database(database, name):
    assert name.startswith("pc_assembly_") and name.removeprefix("pc_assembly_").isalnum()
    async with OceanBaseProfile.open(database, tables=()) as profile:
        async with profile.database.transaction() as connection:
            await connection.exec_driver_sql(f"DROP DATABASE IF EXISTS `{name}`")
        async with profile.database.transaction() as connection:
            count = await connection.scalar(
                text("SELECT count(*) FROM information_schema.SCHEMATA WHERE SCHEMA_NAME = :name"), {"name": name}
            )
            assert count == 0


async def _cleanup(database, scope_ids):
    opener = (
        OceanBaseProfile
        if isinstance(database, OceanBaseConfig)
        else SeekDBProfile
        if isinstance(database, SeekDBConfig)
        else SQLiteProfile
    )
    async with opener.open(database, tables=()) as profile:
        async with profile.database.transaction() as connection:
            scoped_tables = await connection.run_sync(
                lambda conn: [
                    name
                    for name in inspect(conn).get_table_names()
                    if any(column["name"] == "scope_id" for column in inspect(conn).get_columns(name))
                ]
            )
            for name in scoped_tables:
                quoted = connection.dialect.identifier_preparer.quote(name)
                statement = text(f"DELETE FROM {quoted} WHERE scope_id IN :ids").bindparams(  # noqa: S608 - quoted inspected table name
                    bindparam("ids", expanding=True)
                )
                await connection.execute(statement, {"ids": scope_ids})
        async with profile.database.transaction() as connection:
            remaining = 0
            for name in scoped_tables:
                quoted = connection.dialect.identifier_preparer.quote(name)
                statement = text(f"SELECT count(*) FROM {quoted} WHERE scope_id IN :ids").bindparams(  # noqa: S608 - quoted inspected table name
                    bindparam("ids", expanding=True)
                )
                remaining += (await connection.execute(statement, {"ids": scope_ids})).scalar_one()
    assert remaining == 0
    return {"scope_count": len(scope_ids), "remaining_rows": remaining}
