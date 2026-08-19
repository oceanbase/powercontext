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

import asyncio
from pathlib import Path
from typing import cast

import httpx

from powercontext.builtin.artifacts.generation import ArtifactGenerationInput
from powercontext.builtin.artifacts.skill import CodexSkillRoot, SkillContent
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import BuiltinConfig, ExternalSkillsConfig, open_builtin_runtime
from powercontext.client import PowerContextClient
from powercontext.http import (
    ExternalSkillImportMode,
    GeneratedCandidateStatus,
    ImportExternalSkillRequest,
    ListExternalSkillsRequest,
    ResolveExternalSkillRequest,
    ScanExternalSkillsRequest,
)
from powercontext.server.app import ServerApplication, create_app
from powercontext.server.factory import create_server_app
from powercontext.server.settings import McpConfig, ServerSettings


def test_http_sdk_external_skill_registry_preserves_host_local_authority(tmp_path: Path) -> None:
    root = tmp_path / ".agents" / "skills"
    package = root / "friendly-python"
    package.mkdir(parents=True)
    manifest = package / "SKILL.md"
    manifest.write_text(
        "---\nname: friendly-python\ndescription: Use when writing Python.\n---\n\nKeep boundaries explicit.\n",
        encoding="utf-8",
    )
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'registry.db'}"),
            external_skills=ExternalSkillsConfig(
                host_id="workstation-1",
                codex_roots=(
                    CodexSkillRoot(
                        root_id="repository",
                        installation_scope="project",
                        path=root,
                    ),
                ),
            ),
            mcp=McpConfig(enabled=False),
        )
    )

    async def scenario() -> None:
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
            ) as transport,
        ):
            client = PowerContextClient("http://testserver", http_client=transport)
            capabilities = await client.get_capabilities()
            assert capabilities.external_skill_registry is True
            assert capabilities.managed_skill_generation is False

            scan = await client.scan_external_skills(ScanExternalSkillsRequest(scope_id="project:example"))
            registration = scan.registrations[0]
            available = await client.list_external_skills(ListExternalSkillsRequest(scope_id="project:example"))
            assert available.skills[0].entrypoint == str(manifest)

            manifest.write_text(manifest.read_text(encoding="utf-8") + "Changed.\n", encoding="utf-8")

            assert (
                await client.list_external_skills(ListExternalSkillsRequest(scope_id="project:example"))
            ).skills == []
            stale = await client.resolve_external_skill(
                ResolveExternalSkillRequest(
                    scope_id="project:example",
                    external_skill_id=registration.external_skill_id,
                    fingerprint=registration.fingerprint,
                )
            )
            assert stale.status.value == "unavailable"
            assert stale.entrypoint is None

    asyncio.run(scenario())


class _SkillGenerator:
    async def generate(self, _value: ArtifactGenerationInput, /) -> SkillContent:
        return SkillContent(
            name="friendly-python-managed",
            description="Use when writing reviewed Python changes.",
            instructions="Keep boundaries explicit and validate observable behavior.",
            validation=("Focused behavior tests pass.",),
        )


def test_http_sdk_explicitly_imports_exact_external_snapshot_into_review(tmp_path: Path) -> None:
    root = tmp_path / ".agents" / "skills"
    package = root / "friendly-python"
    package.mkdir(parents=True)
    (package / "SKILL.md").write_text(
        "---\nname: friendly-python\ndescription: Use when writing Python.\n---\n\nKeep boundaries explicit.\n",
        encoding="utf-8",
    )
    config = BuiltinConfig(
        database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'import.db'}"),
        external_skills=ExternalSkillsConfig(
            host_id="workstation-1",
            codex_roots=(CodexSkillRoot(root_id="repository", installation_scope="project", path=root),),
        ),
    )

    async def scenario() -> None:
        async with open_builtin_runtime(config, skill_generator=_SkillGenerator()) as runtime:
            app = create_app(application=cast(ServerApplication, runtime))
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
            ) as transport:
                client = PowerContextClient("http://testserver", http_client=transport)
                registration = (
                    await client.scan_external_skills(ScanExternalSkillsRequest(scope_id="project:example"))
                ).registrations[0]

                imported = await client.import_external_skill(
                    ImportExternalSkillRequest(
                        scope_id="project:example",
                        external_skill_id=registration.external_skill_id,
                        fingerprint=registration.fingerprint,
                        mode=ExternalSkillImportMode.IMPORT,
                        reason="Govern a caller-selected external package.",
                    )
                )

                assert imported.status is GeneratedCandidateStatus.PENDING
                assert imported.candidate is not None
                assert imported.candidate.family == "skill"
                assert imported.candidate.source_refs[0].name == "external-skill-snapshot"

    asyncio.run(scenario())
