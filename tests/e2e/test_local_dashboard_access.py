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

"""Use wizard-generated configuration to save through MCP and read in Dashboard."""

import asyncio
import socket
from pathlib import Path

import httpx
import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from pydantic import SecretStr
from typer.testing import CliRunner

from powercontext.builtin.runtime import RuntimeConfig
from powercontext.cli.config import app as config_app
from powercontext.cli.env_file import parse_environment
from powercontext.server.configuration import server_settings_context
from powercontext.server.factory import create_server_app
from powercontext.server.settings import (
    AccessControlConfig,
    BearerAuthConfig,
    DashboardConfig,
    McpConfig,
    ServerSettings,
)
from tests.e2e.atomic_memory_migration_backend import MigrationBackend
from tests.e2e.server_storage import prepare_server_database


@pytest.mark.parametrize("require_authentication", [False, True], ids=["anonymous-default", "authenticated"])
def test_local_wizard_connects_dashboard_api_and_mcp(tmp_path: Path, require_authentication: bool) -> None:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    output = tmp_path / "server.env"
    authentication_answer = "y" if require_authentication else ""
    result = CliRunner().invoke(
        config_app,
        ["init", "--language", "en", "--output", str(output)],
        input=(
            f"sqlite\n{tmp_path / 'memory.db'}\nlocal\nbase\ny\n{port}\n{authentication_answer}\n"
            "codex\ndefault\nnone\ny\n"
        ),
    )
    assert result.exit_code == 0, result.output
    values = parse_environment(output.read_text())
    if not require_authentication:
        assert "POWERCONTEXT_SERVER_AUTH_TOKEN" not in values
        assert "POWERCONTEXT_CLIENT_API_TOKEN" not in values
        assert "POWERCONTEXT_CODEX_AUTHORIZATION" not in values
    base_url = values["POWERCONTEXT_CLIENT_SERVER_URL"]
    headers = {"Authorization": values["POWERCONTEXT_CODEX_AUTHORIZATION"]} if require_authentication else {}
    with server_settings_context(env_file=output) as settings:
        app = create_server_app(settings=settings, scheduler_path=tmp_path / "scheduler.db")

    def create_http_client(
        headers: dict[str, str] | None = None,
        timeout: httpx.Timeout | None = None,
        auth: httpx.Auth | None = None,
        **_: object,
    ) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url=base_url,
            headers=headers,
            timeout=timeout,
            auth=auth,
            follow_redirects=True,
        )

    async def exercise() -> None:
        transport = StreamableHttpTransport(
            f"{base_url}/mcp/", headers=headers, httpx_client_factory=create_http_client
        )
        async with app.router.lifespan_context(app), create_http_client() as browser:
            home = await browser.get("/dashboard/home")
            assert home.status_code == (401 if require_authentication else 200)
            if require_authentication:
                assert (await browser.get("/v1/scopes/default")).status_code == 401
                assert (
                    await browser.get("/v1/scopes/default", headers={"Authorization": "Bearer invalid"})
                ).status_code == 401
                login = await browser.post(
                    "/dashboard/session",
                    headers={"Origin": base_url},
                    data={"token": values["POWERCONTEXT_SERVER_AUTH_TOKEN"]},
                )
                assert login.status_code == 200
            scope = await browser.get("/v1/scopes/default", headers=headers)
            assert scope.status_code == 200
            text = "Local Dashboard authentication is optional."
            async with Client(transport) as mcp:
                saved = await mcp.call_tool(
                    "remember_memory", {"scope_id": scope.json()["scope_id"], "kind": "decision", "text": text}
                )
                assert not saved.is_error
            notes = await browser.get("/dashboard/notes")
            assert notes.status_code == 200
            assert text in notes.text

    asyncio.run(exercise())


@pytest.mark.parametrize("scope_selection", ["explicit", "default"])
def test_dashboard_selected_note_uses_one_policy_and_content_snapshot(
    migration_backend: MigrationBackend, monkeypatch: pytest.MonkeyPatch, scope_selection: str
) -> None:
    from contextvars import Context

    from powercontext.server.authz import AccessAction, BuiltinAuthorizationProvider
    from powercontext.server.dashboard.routes import LABELS

    settings = ServerSettings(
        database=migration_backend.config,
        runtime=RuntimeConfig(artifact_processing_families=()),
        dashboard=DashboardConfig(enabled=True),
        access=AccessControlConfig(mode="enforced"),
        auth=BearerAuthConfig(enabled=True, token=SecretStr("dashboard-snapshot")),
        mcp=McpConfig(enabled=False),
    )
    prepare_server_database(settings, evidence_dir=migration_backend.directory / "migration-evidence")
    app = create_server_app(settings=settings, scheduler_path=migration_backend.directory / "scheduler.db")

    async def scenario() -> None:
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
                headers={"Authorization": "Bearer dashboard-snapshot"},
            ) as client,
        ):
            scope = (await client.get("/v1/scopes/default")).json()["scope_id"]
            created = await client.post(
                f"/v1/scopes/{scope}/artifacts",
                json={"family": "atomic-memory", "content": {"kind": "fact", "text": "Original snapshot body."}},
            )
            assert created.status_code == 201, created.text
            artifact = created.json()["artifact_id"]
            path = f"/v1/scopes/{scope}/artifacts/atomic-memory/{artifact}"
            check = BuiltinAuthorizationProvider.check_batch
            changed = False

            async def interleaved(provider, requests):
                nonlocal changed
                decisions = await check(provider, requests)
                if not changed and any(check.action is AccessAction.SCOPE_READ for check in requests):
                    changed = True

                    async def publish() -> None:
                        revised = await client.put(
                            path,
                            headers={"If-Match": '"revision:1"'},
                            json={"content": {"kind": "fact", "text": "Revised snapshot body."}},
                        )
                        assert revised.status_code == 200, revised.text
                        forgotten = await client.post(
                            "/v1/atomic-memory/lifecycle",
                            json={
                                "scope_id": scope,
                                "target": {
                                    "artifact": {"family": "atomic-memory", "artifact_id": artifact, "revision": 2},
                                    "state_version": 0,
                                },
                                "state": "forgotten",
                            },
                        )
                        assert forgotten.status_code == 200, forgotten.text

                    await asyncio.create_task(publish(), context=Context())
                return decisions

            query = {"scope": scope, "artifact": artifact, "revision": 1, "history": "1", "compare": "2", "lang": "zh"}
            if scope_selection == "default":
                query.pop("scope")
            with monkeypatch.context() as patch:
                patch.setattr(BuiltinAuthorizationProvider, "check_batch", interleaved)
                first = await client.get("/dashboard/notes", params=query)
            assert changed
            assert first.status_code == 200, first.text
            assert "Original snapshot body." in first.text
            assert "Revised snapshot body." not in first.text
            assert LABELS["historical_revision"] not in first.text
            assert LABELS["revision_compare_missing"] in first.text
            current = await client.get("/dashboard/notes", params=query)
            assert current.status_code == 200, current.text
            assert "Original snapshot body." in current.text
            assert LABELS["historical_revision"] in current.text
            assert "+ Revised snapshot body." in current.text
            assert f"{LABELS['current_memory_state']}: {LABELS['memory_state_forgotten']}" in current.text

    asyncio.run(scenario())


def test_review_disclosures_follow_current_read_and_decision_grants(migration_backend: MigrationBackend) -> None:
    import re
    from html import unescape

    settings = ServerSettings(
        database=migration_backend.config,
        runtime=RuntimeConfig(artifact_processing_families=()),
        dashboard=DashboardConfig(enabled=True),
        access=AccessControlConfig(mode="enforced"),
        auth=BearerAuthConfig(token=SecretStr("review-acceptance")),
        mcp=McpConfig(enabled=False),
    )
    prepare_server_database(settings, evidence_dir=migration_backend.directory / "migration-evidence")
    app = create_server_app(settings=settings, scheduler_path=migration_backend.directory / "scheduler.db")

    async def scenario():
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
                headers={"Authorization": "Bearer review-acceptance", "X-PowerContext-Dream-Contract": "2"},
            ) as client,
        ):
            scope = (await client.get("/v1/scopes/default")).json()["scope_id"]
            source = await client.post(f"/v1/scopes/{scope}/sources", json={"content": "Private Review evidence."})
            assert source.status_code == 201, source.text
            skill = await client.post(
                f"/v1/scopes/{scope}/artifacts",
                json={
                    "family": "skill",
                    "content": {
                        "name": "preserved-on-restart",
                        "description": "Read verified evidence",
                        "instructions": "Read evidence before deciding.",
                        "validation": ["Preserve readable evidence."],
                    },
                },
            )
            assert skill.status_code == 201, skill.text
            proposed = await client.post(
                "/v1/experience/propose",
                json={
                    "scope_id": scope,
                    "proposal": {
                        "situation": "Review a delivery",
                        "action": "Check proof",
                        "outcome": "Verified",
                        "lesson": "Check grants",
                    },
                    "source_refs": [{"name": "content", "source_id": source.json()["source_id"]}],
                    "artifact_refs": [],
                },
            )
            assert proposed.status_code == 201, proposed.text
            candidate = proposed.json()["candidate_id"]
            from powercontext.artifacts import ArtifactRef
            from powercontext.builtin.catalog_changes.models import CatalogChangeProposal
            from powercontext.builtin.tags import ArtifactTagTarget
            from powercontext.sources import SourceRef

            artifact = await client.post(
                f"/v1/scopes/{scope}/artifacts",
                json={
                    "family": "experience",
                    "content": proposed.json()["proposal"],
                },
            )
            assert artifact.status_code == 201, artifact.text
            artifact_id = artifact.json()["artifact_id"]
            tags = await client.get(f"/v1/scopes/{scope}/artifacts/experience/{artifact_id}/tags")
            assert tags.status_code == 200, tags.text
            from powercontext.server.authz import PrincipalRef
            from powercontext.server.context import bind_principal, reset_principal

            identity = bind_principal(PrincipalRef(type="service", id="server-token"))
            try:
                tag = await app.state.application.catalog_changes.for_scope(scope).propose(
                    CatalogChangeProposal(
                        target=ArtifactTagTarget(family="experience", artifact_id=artifact_id),
                        expected_etag=tags.headers["ETag"],
                        basis_ref=ArtifactRef(family="experience", artifact_id=artifact_id, revision=1),
                        before_tags=(),
                        after_tags=("verified",),
                    ),
                    sources=(SourceRef(source_type="content", source_id=source.json()["source_id"]),),
                    reason="Verified Tag basis",
                )
            finally:
                reset_principal(identity)
            query = {"scope": scope, "candidate_id": candidate, "lang": "en"}
            directory = await client.get("/dashboard/review", params={"scope": scope, "lang": "en"})
            assert directory.status_code == 200, directory.text
            assert candidate in directory.text and "Review a delivery" not in directory.text
            assert tag.candidate_id in directory.text
            tag_page = await client.get(
                "/dashboard/review", params={**query, "candidate_id": tag.candidate_id, "candidate_kind": "tag"}
            )
            assert tag_page.status_code == 200, tag_page.text
            assert "Verified Tag basis" in tag_page.text
            tag_urls = [unescape(url) for url in re.findall(r'hx-get="([^"]+)"', tag_page.text)]
            tag_history = await client.get(next(url for url in tag_urls if "view=history" in url))
            assert tag_history.status_code == 200 and "Verified Tag basis" in tag_history.text
            page = await client.get("/dashboard/review", params=query)
            assert page.status_code == 200, page.text
            assert "Review a delivery" in page.text and "Approve this version" in page.text
            urls = [unescape(url) for url in re.findall(r'hx-get="([^"]+)"', page.text)]
            history_url = next(url for url in urls if "view=history" in url)
            evidence_url = next(url for url in urls if "evidence=0" in url)
            history = await client.get(history_url)
            assert history.status_code == 200 and "Version 1" in history.text
            assert "Review a delivery" not in history.text and "Private Review evidence." not in history.text
            evidence = await client.get(evidence_url)
            assert evidence.status_code == 200 and "Private Review evidence." in evidence.text
            bindings = (
                await client.post(
                    "/v1/access/bindings/list", json={"management_resource": {"type": "scope", "scope_id": scope}}
                )
            ).json()["items"]

            async def revoke(role):
                binding = next(item for item in bindings if item["role"] == role)
                result = await client.post(
                    "/v1/access/bindings/revoke",
                    json={
                        "binding_id": binding["binding_id"],
                        "expected_version": binding["version"],
                        "idempotency_key": "revoke-" + role,
                    },
                )
                assert result.status_code == 200, result.text

            await revoke("scope.reviewer")
            readonly = await client.get("/dashboard/review", params=query)
            assert readonly.status_code == 200 and "Review a delivery" in readonly.text
            assert "Approve this version" not in readonly.text
            assert (await client.get(history_url)).status_code == 200
            assert (await client.get(evidence_url)).status_code == 200
            denied = await client.post(
                "/dashboard/review/decision",
                headers={"Origin": "http://test"},
                data={
                    "scope": scope,
                    "candidate_kind": "artifact",
                    "candidate_id": candidate,
                    "expected_version": "1",
                    "action": "approve",
                },
            )
            assert denied.status_code == 403, denied.text
            current = await client.post("/v1/candidates/get", json={"scope_id": scope, "candidate_id": candidate})
            assert current.json()["status"] == "pending"
            for role in ("scope.contributor", "scope.viewer", "scope.delegator"):
                await revoke(role)
            for url in (history_url, evidence_url):
                forbidden = await client.get(url)
                assert forbidden.status_code == 403, forbidden.text
                assert "Private Review evidence." not in forbidden.text
                assert "Review a delivery" not in forbidden.text

        restarted = create_server_app(settings=settings, scheduler_path=migration_backend.directory / "scheduler.db")
        async with (
            restarted.router.lifespan_context(restarted),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=restarted),
                base_url="http://test",
                headers={"Authorization": "Bearer review-acceptance", "X-PowerContext-Dream-Contract": "2"},
            ) as client,
        ):
            assert (await client.get(history_url)).status_code == 403
            exact = await client.get(f"/v1/scopes/{scope}/artifacts/skill/{skill.json()['artifact_id']}/revisions/1")
            assert exact.status_code == 200 and exact.json()["content"]["name"] == "preserved-on-restart"
            granted = await client.post(
                "/v1/access/bindings/create",
                json={
                    "subject": {"type": "service", "id": "server-token"},
                    "resource": {"type": "scope", "scope_id": scope},
                    "role": "scope.viewer",
                    "idempotency_key": "restore-review-read",
                },
            )
            assert granted.status_code == 201, granted.text
            assert (await client.get(history_url)).status_code == 200
            library = await client.get("/dashboard/methods", params={"scope": scope, "kind": "skill"})
            assert library.status_code == 200 and "preserved-on-restart" in library.text

    asyncio.run(scenario())
