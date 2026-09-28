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

"""Exercise the Codex full-profile operations through the public MCP transport."""

import asyncio
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

from powercontext.builtin.artifacts.experience import ExperienceContent
from powercontext.builtin.artifacts.generation import ArtifactGenerationInput
from powercontext.builtin.artifacts.skill import CodexSkillRoot, SkillContent
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import BuiltinConfig, ExternalSkillsConfig, open_builtin_runtime
from powercontext.server.app import ServerApplication, create_app
from powercontext.server.mcp import create_mcp_server, mount_mcp


class ExperienceGenerator:
    async def generate(self, _value: ArtifactGenerationInput, /) -> ExperienceContent:
        return ExperienceContent(
            situation="The public API changes.",
            action="Regenerate the client and run contract tests.",
            outcome="The generated client matches the API.",
            lesson="Validate generated contracts before release.",
        )


class SkillGenerator:
    async def generate(self, _value: ArtifactGenerationInput, /) -> SkillContent:
        return SkillContent(
            name="verify-contract",
            description="Use when changing the public API.",
            instructions="Regenerate the client and run contract tests.",
            validation=("Contract tests pass.",),
        )


def test_mcp_full_profile_preserves_review_and_external_fingerprints(tmp_path: Path) -> None:
    root = tmp_path / ".agents" / "skills"
    package = root / "external-contract"
    package.mkdir(parents=True)
    entrypoint = package / "SKILL.md"
    entrypoint.write_text(
        "---\nname: external-contract\ndescription: Use when checking API contracts.\n---\n\nRun contract tests.\n",
        encoding="utf-8",
    )
    config = BuiltinConfig(
        database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'full.db'}"),
        external_skills=ExternalSkillsConfig(
            host_id="codex-workstation",
            codex_roots=(CodexSkillRoot(root_id="project", installation_scope="project", path=root),),
        ),
    )

    async def scenario() -> None:
        async with open_builtin_runtime(
            config, experience_generator=ExperienceGenerator(), skill_generator=SkillGenerator()
        ) as runtime:
            app = create_app(application=cast(ServerApplication, runtime))
            mount_mcp(app)

            def http_client_factory(
                headers: dict[str, str] | None = None,
                timeout: httpx.Timeout | None = None,
                auth: httpx.Auth | None = None,
                **_: object,
            ) -> httpx.AsyncClient:
                return httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app),
                    base_url="http://testserver",
                    headers=headers,
                    timeout=timeout,
                    auth=auth,
                    follow_redirects=True,
                )

            transport = StreamableHttpTransport("http://testserver/mcp/", httpx_client_factory=http_client_factory)
            async with app.router.lifespan_context(app), Client(transport) as client:

                async def call(name: str, **arguments: Any) -> dict[str, Any]:
                    result = await client.call_tool(name, arguments)
                    assert not result.is_error
                    assert result.structured_content is not None
                    return result.structured_content

                scope = await call(
                    "create_scope", title="Codex full", summary="MCP acceptance", idempotency_key="codex-full"
                )
                scope_id = scope["scope_id"]
                source = await call(
                    "capture_content_source",
                    scope_id=scope_id,
                    source_id="verified-contract",
                    content="Regenerated the client and verified that contract tests passed.",
                )
                evidence = {"source_refs": [source["source"]], "artifact_refs": []}
                for family in ("experience", "skill"):
                    extra = {"origin": "source"} if family == "skill" else {}
                    generated = await call(f"generate_{family}", scope_id=scope_id, **evidence, **extra)
                    assert generated["status"] == "pending"
                    candidate = generated["candidate"]
                    inspected = await call(
                        "get_artifact_candidate", scope_id=scope_id, candidate_id=candidate["candidate_id"]
                    )
                    assert inspected["status"] == "pending"
                    assert inspected["result_artifact"] is None
                    approved = await call(
                        "approve_artifact_candidate",
                        scope_id=scope_id,
                        candidate_id=candidate["candidate_id"],
                        expected_version=candidate["version"],
                    )
                    exact = await call(f"get_{family}", scope_id=scope_id, artifact=approved["result_artifact"])
                    assert exact["content"] == candidate["proposal"]

                library = await call("list_managed_skills", scope_id=scope_id)
                assert len(library["skills"]) == 1
                for family, proposal in (
                    (
                        "experience",
                        {
                            "situation": "The public API changes.",
                            "action": "Run contract tests.",
                            "outcome": "The tests pass.",
                            "lesson": "Verify the public API.",
                        },
                    ),
                    (
                        "skill",
                        {
                            "name": "review-contract",
                            "description": "Use when reviewing an API change.",
                            "instructions": "Inspect generated code and run contract tests.",
                            "validation": ["Contract tests pass."],
                        },
                    ),
                ):
                    proposed = await call(f"propose_{family}", scope_id=scope_id, proposal=proposal, **evidence)
                    assert proposed["status"] == "pending"

                scanned = await call("scan_external_skills", scope_id=scope_id)
                registration = scanned["registrations"][0]
                identity = {key: registration[key] for key in ("external_skill_id", "fingerprint")}
                listed = await call("list_external_skills", scope_id=scope_id)
                assert listed["skills"][0]["entrypoint"] == str(entrypoint)
                resolved = await call("resolve_external_skill", scope_id=scope_id, **identity)
                assert resolved["status"] == "available"
                imported = await call("import_external_skill", scope_id=scope_id, mode="import", **identity)
                assert imported["status"] == "pending"
                assert imported["candidate"]["result_artifact"] is None
                assert imported["candidate"]["source_refs"][0]["name"] == "external-skill-snapshot"

                entrypoint.write_text(entrypoint.read_text(encoding="utf-8") + "Changed.\n", encoding="utf-8")
                stale = await call("resolve_external_skill", scope_id=scope_id, **identity)
                assert stale["status"] == "unavailable"
                assert stale["entrypoint"] is None
                failed = await client.call_tool(
                    "import_external_skill", {"scope_id": scope_id, "mode": "import", **identity}, raise_on_error=False
                )
                assert failed.is_error

    asyncio.run(scenario())


class NoOpGenerator:
    async def generate(self, _value: ArtifactGenerationInput, /) -> None:
        return None


@pytest.mark.parametrize("configured", [False, True])
def test_mcp_generation_distinguishes_missing_model_from_no_op(configured: bool) -> None:
    async def scenario() -> None:
        generator = NoOpGenerator() if configured else None
        async with open_builtin_runtime(
            BuiltinConfig(database=SQLiteConfig()), experience_generator=generator, skill_generator=generator
        ) as runtime:
            app = create_app(application=cast(ServerApplication, runtime))
            async with Client(create_mcp_server(app)) as client:
                scope = await client.call_tool(
                    "create_scope",
                    {"title": "No candidates", "summary": "Generation outcomes", "idempotency_key": "noop"},
                )
                scope_id = scope.structured_content["scope_id"]
                source = await client.call_tool(
                    "capture_content_source",
                    {"scope_id": scope_id, "source_id": "task", "content": "No reusable result."},
                )
                for family in ("experience", "skill"):
                    arguments = {
                        "scope_id": scope_id,
                        "source_refs": [source.structured_content["source"]],
                        "artifact_refs": [],
                    }
                    if family == "skill":
                        arguments["origin"] = "source"
                    result = await client.call_tool(f"generate_{family}", arguments, raise_on_error=False)
                    if configured:
                        assert not result.is_error
                        assert result.structured_content["status"] == "no_op"
                        assert result.structured_content["candidate"] is None
                    else:
                        assert result.is_error
                        assert "503" in result.content[0].text
                inbox = await client.call_tool("list_artifact_candidates", {"scope_id": scope_id})
                assert inbox.structured_content["candidates"] == []

    asyncio.run(scenario())
