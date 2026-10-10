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

import asyncio
import base64
import hashlib
import io
import json
import shlex
import shutil
import subprocess
import sys
import zipfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
from pydantic import SecretStr

from examples.systemone.adapter import SystemOneConfig, SystemOneDecisionModel
from examples.systemone.applicability import DecisionApplicabilitySelector, SelectionRequest, SelectionResult
from examples.systemone.applicability_catalog import ServerCandidateCatalog, StaleRecommendationError
from examples.systemone.applicability_eval import evaluate_cases
from examples.systemone.applicability_fixture import seed_fixture, skill_archive
from examples.systemone.applicability_host import run_codex_host
from powercontext.builtin.artifacts.skill import capture_skill_archive
from powercontext.builtin.artifacts.skill.external import AgentEnvironmentProfile, AgentSkillTarget
from powercontext.builtin.inference import InferenceUsage
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import (
    BuiltinConfig,
    DecisionOutcome,
    DecisionRequest,
    DecisionResult,
    RuntimeConfig,
    open_builtin_contexts,
)
from powercontext.builtin.runtime.config import ExternalSkillsConfig
from powercontext.builtin.sources.content import ContentCapture
from powercontext.builtin.sources.skill_usage import SkillUsageCapture
from powercontext.client import ForbiddenResponseError, PowerContextClient
from powercontext.http import (
    ApproveCandidateRequest,
    ArtifactReference,
    CaptureContentSourceRequest,
    ExperienceProposal,
    ProposeExperienceRequest,
    ProposeSkillPackageRequest,
    RejectCandidateRequest,
    SkillLifecycleState,
    UpdateSkillLifecycleRequest,
)
from powercontext.server.authentication import AuthenticationResult, ProviderReadiness
from powercontext.server.authz import PrincipalRef
from powercontext.server.authz.composition import open_builtin_access_control
from powercontext.server.factory import create_server_app
from powercontext.server.settings import AccessControlConfig, McpConfig, MetricsConfig, ServerSettings


class Authentication:
    async def authenticate(self, request):
        identity = request.headers.get("authorization", "Bearer admin").removeprefix("Bearer ")
        return AuthenticationResult(subject=PrincipalRef(type="service", id=identity))

    async def readiness(self):
        return ProviderReadiness(ready=True)


@asynccontextmanager
async def server(directory: Path) -> AsyncIterator[tuple[PowerContextClient, httpx.AsyncClient]]:
    database = SQLiteConfig(url=f"sqlite+aiosqlite:///{directory / 'selection.db'}")
    async with open_builtin_access_control(
        database,
        bootstrap_administrators=(PrincipalRef(type="service", id="admin"),),
        deployment_id="selection",
    ) as access:
        app = create_server_app(
            settings=ServerSettings(
                database=database,
                runtime=RuntimeConfig(artifact_processing_families=()),
                external_skills=ExternalSkillsConfig(),
                access=AccessControlConfig(mode="enforced", deployment_id="selection"),
                mcp=McpConfig(enabled=False),
                metrics=MetricsConfig(enabled=False),
            ),
            authentication_provider=Authentication(),
            access_control=access,
            scheduler_path=directory / "scheduler.db",
        )
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as http,
            PowerContextClient("http://localhost", token="admin", http_client=http) as client,  # noqa: S106
        ):
            yield client, http


def target(directory: Path) -> AgentSkillTarget:
    return AgentSkillTarget(
        target_id="codex-project",
        agent_kind="codex",
        installation_scope="project",
        path=directory / "skills",
        environment=AgentEnvironmentProfile(
            operating_system="windows", architecture="x86_64", commands={"python": "3.12"}
        ),
    )


async def propose(client: PowerContextClient, scope_id: str, name: str, *, incompatible: bool = False, target_ref=None):
    return await client.propose_skill_package(
        ProposeSkillPackageRequest(
            scope_id=scope_id,
            archive_base64=base64.b64encode(
                skill_archive(
                    name,
                    "HTTP contract synthetic procedure.",
                    "Only modify an HTTP contract.",
                    incompatible=incompatible,
                )
            ).decode(),
            target=target_ref,
        )
    )


async def approve(client: PowerContextClient, scope_id: str, pending) -> ArtifactReference:
    approved = await client.approve_candidate(
        ApproveCandidateRequest(
            scope_id=scope_id,
            candidate_id=pending.candidate_id,
            expected_version=pending.version,
        )
    )
    assert approved.result_artifact is not None
    return approved.result_artifact


def test_authorized_catalog_excludes_unreviewed_retired_and_incompatible_packages(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with server(tmp_path) as (client, _):
            scope_id, references = await seed_fixture(client)
            await propose(client, scope_id, "http-contract-pending")
            rejected = await propose(client, scope_id, "http-contract-rejected")
            await client.reject_candidate(
                RejectCandidateRequest(
                    scope_id=scope_id,
                    candidate_id=rejected.candidate_id,
                    expected_version=rejected.version,
                    reason="Synthetic package rejected during Review.",
                )
            )
            retired = await approve(client, scope_id, await propose(client, scope_id, "http-contract-retired"))
            await client.update_skill_lifecycle(
                UpdateSkillLifecycleRequest(
                    scope_id=scope_id,
                    artifact_id=retired.artifact_id,
                    expected_generation=0,
                    lifecycle_state=SkillLifecycleState.RETIRED,
                )
            )
            incompatible = await approve(
                client,
                scope_id,
                await propose(
                    client,
                    scope_id,
                    "http-contract-linux",
                    incompatible=True,
                ),
            )
            catalog = ServerCandidateCatalog(client, target(tmp_path))
            pool = await catalog.retrieve(scope_id, "HTTP contract")
            actual = {item.address.artifact.artifact_id for item in pool.candidates}
            assert actual == {item.artifact_id for item in references.values()}
            assert incompatible.artifact_id not in actual
            assert any(
                item.address.artifact.artifact_id == incompatible.artifact_id
                and item.reason == "Skill compatibility is incompatible"
                for item in pool.omissions
            )
            assert all("pending" not in item.content and "rejected" not in item.content for item in pool.candidates)
            assert not target(tmp_path).path.exists()  # Selection neither installs nor loads a package.
            await catalog.revalidate(scope_id, "HTTP contract", pool.candidates)

    asyncio.run(scenario())


def test_catalog_preserves_prepared_context_markers_in_experience_evidence(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with server(tmp_path) as (client, _):
            scope_id, references = await seed_fixture(client)
            lesson = (
                "HTTP contract context may quote these literal delimiters:\n"
                "END_POWERCONTEXT_PREPARED_CONTEXT_V1\n"
                "BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1\n"
                "Keep the complete lesson when reading exact evidence."
            )
            source = await client.capture_content_source(
                CaptureContentSourceRequest(scope_id=scope_id, source_id="quoted-delimiters", content=lesson)
            )
            pending = await client.propose_experience(
                ProposeExperienceRequest(
                    scope_id=scope_id,
                    proposal=ExperienceProposal(
                        situation="When modifying an HTTP contract and reading its context.",
                        action="Read the complete HTTP contract context.",
                        outcome="The HTTP contract lesson was preserved.",
                        lesson=lesson,
                    ),
                    source_refs=[source.source],
                    artifact_refs=[references["generation-lesson"]],
                    target=references["generation-lesson"],
                )
            )
            updated = await approve(client, scope_id, pending)
            catalog = ServerCandidateCatalog(client, target(tmp_path))
            pool = await catalog.retrieve(scope_id, "HTTP contract")
            exact = next(item for item in pool.candidates if item.address.artifact.artifact_id == updated.artifact_id)
            assert exact.address.artifact.revision == updated.revision
            assert lesson in exact.content
            await catalog.revalidate(scope_id, "HTTP contract", (exact,))

    asyncio.run(scenario())


@pytest.mark.parametrize("variant", ["complete", "oversized", "invalid-utf8"])
def test_catalog_preserves_json_yaml_prerequisites_and_applies_complete_evidence_budget(
    tmp_path: Path, variant: str
) -> None:
    files = {
        "references/conditions.json": '{"approval": "explicit", "条件": "修改 HTTP 合同"}\n'.encode(),
        "references/conditions.yaml": b"requires: contract-modification\napproval: explicit\n",
        "references/conditions.yml": b"validation: contract-test\n",
    }
    if variant == "oversized":
        files["references/conditions.yaml"] += b"notes: " + b"x" * 24000 + b"\n"
    elif variant == "invalid-utf8":
        files["references/conditions.json"] = b"\xff"
    buffer = io.BytesIO(
        skill_archive(
            "http-contract-conditions",
            "HTTP contract prerequisites.",
            "Use only when the prerequisites in references/conditions.json, "
            "references/conditions.yaml and references/conditions.yml are satisfied.",
        )
    )
    with zipfile.ZipFile(buffer, "a") as archive:
        for path, content in files.items():
            archive.writestr(path, content)
    package = capture_skill_archive(buffer.getvalue())

    class Judge:
        policy_id = "simulated.complete-evidence"

        def __init__(self) -> None:
            self.requests: list[DecisionRequest] = []

        async def evaluate(self, request: DecisionRequest, /) -> DecisionResult:
            self.requests.append(request)
            return DecisionResult(
                outcome=DecisionOutcome.YES, policy_id=self.policy_id, usage=InferenceUsage(requests=1)
            )

    async def scenario() -> None:
        async with server(tmp_path) as (client, _):
            scope_id, _ = await seed_fixture(client)
            pending = await client.propose_skill_package(
                ProposeSkillPackageRequest(
                    scope_id=scope_id, archive_base64=base64.b64encode(buffer.getvalue()).decode()
                )
            )
            reference = await approve(client, scope_id, pending)
            catalog = ServerCandidateCatalog(client, target(tmp_path))
            pool = await catalog.retrieve(scope_id, "HTTP contract")
            selected = tuple(
                item for item in pool.candidates if item.address.artifact.artifact_id == reference.artifact_id
            )
            if variant == "invalid-utf8":
                assert selected == ()
                assert any(
                    item.address.artifact.artifact_id == reference.artifact_id
                    and item.reason == "complete Skill text evidence is not UTF-8"
                    for item in pool.omissions
                )
                return
            assert len(selected) == 1
            candidate = selected[0]
            assert candidate.address.artifact.model_dump(mode="json") == reference.model_dump(mode="json")
            assert candidate.package_digest == "sha256:" + package.reference.tree_digest
            assert not any(item.address == candidate.address for item in pool.omissions)
            evidence = json.loads(candidate.content)
            for path, content in files.items():
                assert evidence["files"][path] == content.decode("utf-8")
            await catalog.revalidate(scope_id, "HTTP contract", selected)
            model = Judge()
            result = await DecisionApplicabilitySelector(model, enabled=True).select(
                SelectionRequest(task="Change the HTTP contract with explicit approval.", candidates=selected)
            )
            assert result.used_fallback is False
            if variant == "oversized":
                assert model.requests == []
                assert result.recommendations == ()
                assert result.assessments[0].outcome is DecisionOutcome.ABSTAIN
                assert result.assessments[0].policy_id == "local.input-budget"
            else:
                assert result.recommendations == (candidate.address,)
                assert model.requests[0].evidence == (candidate.content,)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "version,expected", [("3.12", "incompatible"), ("unknown", "manual_review_required"), ("3.14", None)]
)
def test_runtime_version_compatibility_is_decided_before_applicability(
    tmp_path: Path, version: str, expected: str | None
) -> None:
    async def scenario() -> None:
        async with server(tmp_path) as (client, _):
            scope_id, _ = await seed_fixture(client)
            constrained = await approve(
                client, scope_id, await propose(client, scope_id, "http-contract-linux", incompatible=True)
            )
            compatible_target = target(tmp_path).model_copy(
                update={
                    "environment": AgentEnvironmentProfile(
                        operating_system="linux", architecture="x86_64", commands={"python": version}
                    )
                }
            )
            pool = await ServerCandidateCatalog(client, compatible_target).retrieve(scope_id, "HTTP contract")
            included = any(item.address.artifact.artifact_id == constrained.artifact_id for item in pool.candidates)
            assert included == (expected is None)
            if expected is not None:
                assert any(item.reason == f"Skill compatibility is {expected}" for item in pool.omissions)

    asyncio.run(scenario())


def test_paired_report_uses_same_actual_server_pool_and_marks_simulated_selection_as_non_execution(
    tmp_path: Path,
) -> None:
    def provider(incoming: httpx.Request) -> httpx.Response:
        assert incoming.method == "POST"
        assert str(incoming.url) == "https://openrouter.ai/api/alpha/decisions"
        assert incoming.headers["authorization"] == "Bearer test-key"
        body = json.loads(incoming.content)
        assert body["model"] == "typesafe/jev-1.13"
        state = json.loads(body["state"])
        assert state["evidence"]
        return httpx.Response(
            200,
            json={
                "answers": {
                    "decision": {
                        "type": "choice",
                        "choice": "no",
                        "probabilities": {"yes": 0, "no": 1, "abstain": 0},
                    }
                },
                "usage": {"input_tokens": 20, "output_tokens": 3},
            },
        )

    async def scenario() -> None:
        async with server(tmp_path) as (client, _), httpx.AsyncClient(transport=httpx.MockTransport(provider)) as http:
            scope_id, references = await seed_fixture(client)
            model = SystemOneDecisionModel(
                SystemOneConfig(
                    provider="jev",
                    endpoint="https://openrouter.ai/api/alpha/decisions",
                    model="typesafe/jev-1.13",
                    api_key=SecretStr("test-key"),
                ),
                http,
            )
            output = tmp_path / "reports"
            output.mkdir()
            records = await evaluate_cases(
                client,
                ServerCandidateCatalog(client, target(tmp_path)),
                model,
                scope_id,
                references,
                (),
                output_directory=output,
                input_price_per_million=2,
                output_price_per_million=4,
            )
            by_name = {item["case"]: cast(dict[str, Any], item) for item in records}
            change = by_name["change-en"]
            assert change["baseline"]["result"]["pool_digest"] == change["assisted"]["result"]["pool_digest"]
            assert change["assisted"]["metrics"]["applicable_candidate_recall"] == 0
            explanation = by_name["explain-en"]
            assert explanation["baseline"]["metrics"]["unnecessary_recommendations"] == 3
            assert explanation["assisted"]["metrics"]["selection_exact"]
            assert explanation["assisted"]["metrics"]["task_success"] is None
            assert explanation["assisted"]["metrics"]["wrong_loads"] is None
            assert explanation["hosts"] == {}
            assert explanation["estimated_added_cost_usd"] == pytest.approx(0.00026)
            assert len((output / "cases.jsonl").read_text(encoding="utf-8").splitlines()) == len(records)
            assert "解释" in (output / "explain-zh.json").read_text(encoding="utf-8")

    asyncio.run(scenario())


def test_unauthorized_scope_fails_before_model_evaluation(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with server(tmp_path) as (admin, http):
            scope_id, _ = await seed_fixture(admin)
            async with PowerContextClient("http://localhost", token="outsider", http_client=http) as outsider:  # noqa: S106
                catalog = ServerCandidateCatalog(outsider, target(tmp_path))
                with pytest.raises(ForbiddenResponseError):
                    await catalog.retrieve(scope_id, "HTTP contract")

    asyncio.run(scenario())


def test_exact_recommendation_is_rejected_after_revision_or_lifecycle_changes(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with server(tmp_path) as (client, _):
            scope_id, references = await seed_fixture(client)
            catalog = ServerCandidateCatalog(client, target(tmp_path))
            pool = await catalog.retrieve(scope_id, "HTTP contract")
            old = references["http-contract-generation"]
            selected = tuple(item for item in pool.candidates if item.address.artifact.artifact_id == old.artifact_id)
            updated = await approve(
                client,
                scope_id,
                await propose(
                    client,
                    scope_id,
                    "http-contract-generation",
                    target_ref=old,
                ),
            )
            assert updated.revision == old.revision + 1
            with pytest.raises(StaleRecommendationError):
                await catalog.revalidate(scope_id, "HTTP contract", selected)
            fresh = await catalog.retrieve(scope_id, "HTTP contract")
            selected = tuple(
                item for item in fresh.candidates if item.address.artifact.artifact_id == updated.artifact_id
            )
            await client.update_skill_lifecycle(
                UpdateSkillLifecycleRequest(
                    scope_id=scope_id,
                    artifact_id=updated.artifact_id,
                    expected_generation=0,
                    lifecycle_state=SkillLifecycleState.DEPRECATED,
                )
            )
            with pytest.raises(StaleRecommendationError):
                await catalog.revalidate(scope_id, "HTTP contract", selected)

    asyncio.run(scenario())


def test_actual_server_reads_feed_exact_versions_to_the_existing_decision_port(tmp_path: Path) -> None:
    class Judge:
        policy_id = "simulated.known-version"

        async def evaluate(self, request: DecisionRequest, /) -> DecisionResult:
            assert "HTTP" in request.evidence[0]
            return DecisionResult(
                outcome=DecisionOutcome.NO, policy_id=self.policy_id, usage=InferenceUsage(requests=1)
            )

    async def scenario() -> None:
        async with server(tmp_path) as (client, _):
            scope_id, _ = await seed_fixture(client)
            pool = await ServerCandidateCatalog(client, target(tmp_path)).retrieve(scope_id, "HTTP contract")
            request = SelectionRequest(task="只解释 HTTP 接口用途。", candidates=pool.candidates)
            baseline = await DecisionApplicabilitySelector().select(request)
            assisted = await DecisionApplicabilitySelector(Judge(), enabled=True).select(request)
            assert baseline.recommendations
            assert assisted.recommendations == ()
            assert assisted.status == "none"
            assert baseline.pool_digest == assisted.pool_digest
            assert tuple(item.address for item in assisted.assessments) == tuple(
                item.address for item in pool.candidates
            )

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "invocation,tamper_tests,selection",
    [
        ("script", False, "baseline"),
        ("module", False, "baseline"),
        ("shell-script", False, "baseline"),
        ("shell-module", False, "baseline"),
        ("none", False, "baseline"),
        ("echo", False, "baseline"),
        ("inline-code", False, "baseline"),
        ("read-script", False, "baseline"),
        ("unrelated-module", False, "baseline"),
        ("missing-markers", False, "baseline"),
        ("failed-command", False, "baseline"),
        ("final-failed", False, "baseline"),
        ("final-missing-marker", False, "baseline"),
        ("final-unknown-exit", False, "baseline"),
        ("host-failed", False, "baseline"),
        ("module", True, "baseline"),
        ("module", False, "none"),
        ("module", False, "deployment"),
    ],
)
def test_simulated_host_trace_records_exact_usage_without_crediting_selection_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, invocation: str, tamper_tests: bool, selection: str
) -> None:
    """A simulated CLI trace protects evidence recording; this is not real host acceptance."""

    def simulated_codex(directory: Path, task: str, timeout: int):
        assert "request_id" in task
        schema_path = directory / "openapi/powercontext.yaml"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        schema["components"]["schemas"]["StatusResponse"]["properties"]["request_id"] = {"type": "string"}
        schema_path.write_text(json.dumps(schema), encoding="utf-8")
        events = []
        for script in ("read_context.py", "generate.py", "contract_test.py"):
            module = script.removesuffix(".py")
            command = (
                [sys.executable, script] if invocation.endswith("script") else [sys.executable, "-u", "-m", module]
            )
            process = subprocess.run(
                command, cwd=directory, capture_output=True, text=True, encoding="utf-8", check=True
            )
            recorded = subprocess.list2cmdline(command) if sys.platform == "win32" else shlex.join(command)
            shell_command = (
                f"pwsh.exe -Command \"& '{sys.executable}' {' '.join(command[1:])}\""
                if sys.platform == "win32"
                else "/bin/bash -lc " + shlex.quote(recorded)
            )
            recorded = {
                "shell-script": shell_command,
                "shell-module": shell_command,
                "echo": "echo " + recorded,
                "inline-code": f"python -c \"print('{script} -m {module}')\"",
                "read-script": f"pwsh.exe -Command \"Get-Content -LiteralPath '{script}'\"",
                "unrelated-module": f"python -m unrelated.{module}",
            }.get(invocation, recorded)
            if invocation != "none":
                events.append({
                    "type": "item.completed",
                    "item": {
                        "type": "command_execution",
                        "command": recorded,
                        "exit_code": 1 if invocation == "failed-command" else process.returncode,
                        "aggregated_output": "" if invocation == "missing-markers" else process.stdout,
                    },
                })
        if tamper_tests:
            (directory / "contract_test.py").write_text("print('pretend success')\n", encoding="utf-8")
        if invocation.startswith("final-"):
            client_path = directory / "client.py"
            verified_client = client_path.read_text(encoding="utf-8")
            if invocation == "final-failed":
                client_path.write_text(verified_client.replace("NotRequired[str]", "str"), encoding="utf-8")
            command = [sys.executable, "-m", "contract_test"]
            process = subprocess.run(
                command, cwd=directory, capture_output=True, text=True, encoding="utf-8", check=False
            )
            client_path.write_text(verified_client, encoding="utf-8")
            events.append({
                "type": "item.completed",
                "item": {
                    "type": "command_execution",
                    "command": subprocess.list2cmdline(command) if sys.platform == "win32" else shlex.join(command),
                    "exit_code": None if invocation == "final-unknown-exit" else process.returncode,
                    "aggregated_output": "" if invocation == "final-missing-marker" else process.stdout,
                },
            })
        return (1 if invocation == "host-failed" else 0), events

    monkeypatch.setattr("examples.systemone.applicability_host._invoke_codex", simulated_codex)

    async def scenario() -> None:
        async with server(tmp_path) as (client, _):
            scope_id, references = await seed_fixture(client)
            catalog = ServerCandidateCatalog(client, target(tmp_path))
            pool = await catalog.retrieve(scope_id, "HTTP contract")
            request = SelectionRequest(task="Add optional request_id to the HTTP contract", candidates=pool.candidates)
            result = await DecisionApplicabilitySelector().select(request)
            if selection != "baseline":
                recommendations = (
                    tuple(
                        item.address
                        for item in pool.candidates
                        if item.address.artifact.artifact_id == references["http-contract-deployment"].artifact_id
                    )
                    if selection == "deployment"
                    else ()
                )
                result = SelectionResult(
                    mode="decision",
                    status="selected" if recommendations else "none",
                    recommendations=recommendations,
                    pool_digest=request.pool_digest,
                )
            evidence = await run_codex_host(
                client, catalog, scope_id, "HTTP contract", request, result, tmp_path / "host"
            )
            observe_commands = invocation in {
                "script",
                "module",
                "shell-script",
                "shell-module",
                "host-failed",
                "final-failed",
                "final-missing-marker",
                "final-unknown-exit",
            }
            task_success = (
                observe_commands
                and not tamper_tests
                and invocation
                not in {
                    "host-failed",
                    "final-failed",
                    "final-missing-marker",
                    "final-unknown-exit",
                }
            )
            assert evidence["task_success"] == task_success
            assert evidence["workspace_verified"] == (not tamper_tests)
            invoked = observe_commands and selection == "baseline"
            assert evidence["skill_invocation_observed"] == invoked
            sources = cast(list[dict[str, str]], evidence["skill_usage_sources"])
            assert len(sources) == (0 if selection == "none" else 1)
            if not sources:
                return
            selected = next(
                item for item in pool.candidates if item.address in result.recommendations and item.package_digest
            )
        # Inspect the registered adapter Source through its supported typed catalog and read API.
        async with open_builtin_contexts(
            BuiltinConfig(
                database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'selection.db'}"),
                runtime=RuntimeConfig(artifact_processing_families=()),
            )
        ) as contexts:
            context = await contexts.get(scope_id)
            stored = next(item for item in await context.sources.list() if item.name == sources[0]["source_id"])
            persisted = await context.sources.get(stored)
            usage = await context.sources.read(persisted)
            assert isinstance(usage, SkillUsageCapture)
            assert usage.skill_ref.model_dump(mode="json") == selected.address.artifact.model_dump(mode="json")
            assert usage.package_digest == selected.package_digest
            assert usage.selected is True
            assert usage.invoked.value == ("true" if invoked else "unknown")
            expected_validation = (
                (
                    "failed"
                    if tamper_tests or invocation in {"failed-command", "final-failed"}
                    else "passed"
                    if invoked and invocation not in {"final-missing-marker", "final-unknown-exit"}
                    else "unknown"
                )
                if selection == "baseline"
                else "unknown"
            )
            assert usage.validation.value == expected_validation
            expected_outcome = (
                ("success" if task_success else "failure")
                if invoked and invocation not in {"final-missing-marker", "final-unknown-exit"}
                else "unknown"
            )
            assert usage.outcome.value == expected_outcome
            assert not invoked or usage.outcome.value == evidence["task_outcome"]
            assert usage.task_source is not None
            assert {"name": usage.task_source.source_type, "source_id": usage.task_source.source_id} == evidence[
                "outcome_source"
            ]

    asyncio.run(scenario())


def run_fixture_subprocess(
    directory: Path, script: str, shell: str, *, fallback: str, trace: str
) -> tuple[subprocess.CompletedProcess[str], dict[str, Any]]:
    """Observe a real fixture process using the command/aggregate fields supplied by a host trace."""

    command = [sys.executable, script]
    direct = subprocess.list2cmdline(command) if sys.platform == "win32" else shlex.join(command)
    prefixed_validation = trace.startswith(("passed-then-assignment", "passed-then-env"))
    if sys.platform == "win32" and not prefixed_validation:
        invocation = "& '" + sys.executable.replace("'", "''") + "' " + script
        trailer = f"Get-Content -LiteralPath {script}" if fallback == "cat" else f"Write-Output '{fallback}'"
        shell_command = [shell, "-NoProfile", "-NonInteractive", "-Command", invocation]
    else:
        invocation = shlex.join([sys.executable.replace("\\", "/"), script])
        trailer = f"cat {script}" if fallback == "cat" else "printf '%s\\n' " + shlex.quote(fallback)
        shell_command = [shell, "-c", invocation]
    if fallback:
        if prefixed_validation:
            prefix = {
                "passed-then-assignment": "TRACE=1",
                "passed-then-assignment-quoted": "TRACE='path/with space'",
                "passed-then-env": "env TRACE=1",
                "passed-then-env-quoted": 'env TRACE="/tmp/my path"',
                "passed-then-env-separator": "env -- TRACE=1",
                "passed-then-env-ignore": "env -i TRACE=1",
                "passed-then-env-unset": "env -u TRACE TRACE=1",
            }[trace]
            shell_command[-1] = prefix + " " + shell_command[-1]
        shell_command[-1] += " || " + trailer
        if trace == "passed-then-native-compound":
            harmless = (
                subprocess.list2cmdline([shell, "-Command", "Write-Output harmless"])
                if sys.platform == "win32"
                else shlex.join([shell, "-c", "printf harmless"])
            )
            shell_command[-1] = harmless + "; " + shell_command[-1]
    use_shell = bool(fallback) or trace in {"wrapper-success", "call-success"}
    process = subprocess.run(
        shell_command if use_shell else command,
        cwd=directory,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    recorded = (
        invocation
        if trace == "call-success"
        else (subprocess.list2cmdline(shell_command) if sys.platform == "win32" else shlex.join(shell_command))
        if use_shell
        else direct
    )
    if fallback and trace in {"passed-then-wrapper-args", "passed-then-deep-wrapper"}:
        # These are simulated bash trace shapes; the real failing subprocess uses the available shell.
        wrapped = "bash -c " + shlex.quote("python contract_test.py || cat contract_test.py")
        recorded = (
            wrapped + " trace-name"
            if trace == "passed-then-wrapper-args"
            else "bash -c " + shlex.quote("bash -c " + shlex.quote(wrapped))
        )
    return process, {
        "type": "item.completed",
        "item": {
            "type": "command_execution",
            "command": recorded,
            "exit_code": process.returncode,
            "aggregated_output": process.stdout + process.stderr,
        },
    }


@pytest.mark.parametrize(
    "trace",
    [
        "failed-cat",
        "failed-echo",
        "passed-then-ambiguous",
        "passed-then-wrapper-args",
        "passed-then-deep-wrapper",
        "passed-then-native-compound",
        "passed-then-assignment",
        "passed-then-assignment-quoted",
        "passed-then-env",
        "passed-then-env-quoted",
        "passed-then-env-separator",
        "passed-then-env-ignore",
        "passed-then-env-unset",
        "revalidated",
        "compound-context",
        "compound-generation",
        "wrapper-success",
        "call-success",
    ],
)
def test_host_attributes_fixture_results_before_persisting_reopened_usage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, trace: str
) -> None:
    """Run real fixture/shell subprocesses, but simulate Codex command events and its final exit."""

    prefixed_validation = trace.startswith(("passed-then-assignment", "passed-then-env"))
    shell = shutil.which("pwsh" if sys.platform == "win32" and not prefixed_validation else "bash") or pytest.skip(
        "This process-attribution scenario requires PowerShell or bash"
    )

    def simulated_codex(directory: Path, task: str, timeout: int):
        events = []

        def execute(script: str, *, fallback: str = "", record: bool = True):
            process, event = run_fixture_subprocess(
                directory,
                script,
                shell,
                fallback=fallback,
                trace=trace,
            )
            events.extend([event] if record else [])
            return process

        context_path = directory / "selected-context.json"
        original_context = context_path.read_bytes()
        if trace == "compound-context":
            digest = "sha256:" + hashlib.sha256(original_context).hexdigest()
            context_path.write_text("{}", encoding="utf-8")
            assert execute("read_context.py", record=False).returncode == 1
            process = execute("read_context.py", fallback=f"POWERCONTEXT_CONTEXT_READ {digest}")
            assert process.returncode == 0 and "POWERCONTEXT_CONTEXT_READ" in process.stdout
            context_path.write_bytes(original_context)
        else:
            assert execute("read_context.py").returncode == 0

        schema_path = directory / "openapi/powercontext.yaml"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        schema["components"]["schemas"]["StatusResponse"]["properties"]["request_id"] = {"type": "string"}
        valid_schema = json.dumps(schema)
        if trace == "compound-generation":
            schema_path.write_text("{}", encoding="utf-8")
            assert execute("generate.py", record=False).returncode == 1
            process = execute("generate.py", fallback="CLIENT_GENERATION_PASSED")
            assert process.returncode == 0 and "CLIENT_GENERATION_PASSED" in process.stdout
        schema_path.write_text(valid_schema, encoding="utf-8")
        assert execute("generate.py", record=trace != "compound-generation").returncode == 0

        ambiguous_validation = (
            trace
            in {
                "failed-cat",
                "failed-echo",
                "passed-then-ambiguous",
                "passed-then-wrapper-args",
                "passed-then-deep-wrapper",
                "passed-then-native-compound",
                "revalidated",
            }
            or prefixed_validation
        )
        if ambiguous_validation:
            if trace.startswith("passed-then-") or trace == "revalidated":
                assert execute("contract_test.py").returncode == 0
            client_path = directory / "client.py"
            valid_client = client_path.read_text(encoding="utf-8")
            client_path.write_text(valid_client.replace("NotRequired[str]", "str"), encoding="utf-8")
            # The target process really fails; only the aggregate shell result and marker look successful.
            assert execute("contract_test.py", record=False).returncode == 1
            fallback = "CONTRACT_TEST_PASSED" if trace == "failed-echo" else "cat"
            process = execute("contract_test.py", fallback=fallback)
            assert process.returncode == 0 and "CONTRACT_TEST_PASSED" in process.stdout
            assert "AssertionError" in process.stderr
            client_path.write_text(valid_client, encoding="utf-8")
        if not ambiguous_validation or trace == "revalidated":
            assert execute("contract_test.py").returncode == 0
        return 0, events

    monkeypatch.setattr("examples.systemone.applicability_host._invoke_codex", simulated_codex)

    async def scenario() -> None:
        async with server(tmp_path) as (client, _):
            scope_id, _ = await seed_fixture(client)
            catalog = ServerCandidateCatalog(client, target(tmp_path))
            pool = await catalog.retrieve(scope_id, "HTTP contract")
            request = SelectionRequest(task="Add optional request_id to the HTTP contract", candidates=pool.candidates)
            result = await DecisionApplicabilitySelector().select(request)
            evidence = await run_codex_host(
                client, catalog, scope_id, "HTTP contract", request, result, tmp_path / "host"
            )
            invoked = trace.startswith("passed-then-") or trace in {"revalidated", "wrapper-success", "call-success"}
            validated = trace in {"revalidated", "wrapper-success", "call-success"}
            succeeded = validated or trace == "compound-context"
            assert evidence["workspace_verified"] is True
            assert evidence["context_read_observed"] == (trace != "compound-context")
            assert evidence["generation_observed"] == (trace != "compound-generation")
            assert evidence["skill_invocation_observed"] == invoked
            assert evidence["validation_command_observed"] is True
            assert evidence["final_validation_attributable"] == (
                trace not in {"failed-cat", "failed-echo"} and not trace.startswith("passed-then-")
            )
            assert evidence["workflow_validation"] == ("passed" if validated else "unknown")
            assert evidence["task_success"] == succeeded
            assert evidence["task_outcome"] == ("success" if succeeded else "unknown")
            sources = cast(list[dict[str, str]], evidence["skill_usage_sources"])
            assert len(sources) == 1
            selected = next(
                item for item in pool.candidates if item.address in result.recommendations and item.package_digest
            )
        # Reopen SQLite independently of the HTTP Server, then read the authoritative registered Sources.
        async with open_builtin_contexts(
            BuiltinConfig(
                database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'selection.db'}"),
                runtime=RuntimeConfig(artifact_processing_families=()),
            )
        ) as contexts:
            context = await contexts.get(scope_id)
            stored = await context.sources.list()
            usage_id = next(item for item in stored if item.name == sources[0]["source_id"])
            usage = await context.sources.read(await context.sources.get(usage_id))
            assert isinstance(usage, SkillUsageCapture)
            assert usage.skill_ref.model_dump(mode="json") == selected.address.artifact.model_dump(mode="json")
            assert usage.package_digest == selected.package_digest
            assert usage.selected is True
            assert usage.invoked.value == ("true" if invoked else "unknown")
            assert usage.validation.value == ("passed" if validated else "unknown")
            assert usage.outcome.value == ("success" if validated else "unknown")
            assert usage.task_source is not None
            source_id = next(item for item in stored if item.name == usage.task_source.source_id)
            outcome = await context.sources.read(await context.sources.get(source_id))
            assert isinstance(outcome, ContentCapture)
            assert outcome.source_id == cast(dict[str, str], evidence["outcome_source"])["source_id"]
            captured = json.loads(outcome.content)
            for field in (
                "context_read_observed",
                "generation_observed",
                "final_validation_attributable",
                "workflow_validation",
                "task_success",
                "task_outcome",
            ):
                assert captured[field] == evidence[field]

    asyncio.run(scenario())
