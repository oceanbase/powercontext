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

"""Use a delivered plugin through the installed Server CLI and public HTTP APIs."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import yaml

EXAMPLE = Path(__file__).resolve().parents[2] / "examples/artifact_plugins/incident_report"
SUMMARY_EXAMPLE = EXAMPLE.parent / "ticket_summary"
ADMIN_AUTHORIZATION_VALUE = "artifact-plugin-e2e-admin"
REGISTRATION = "/v1/admin/artifact-plugins/incident-reports"
SUMMARY_REGISTRATION = "/v1/admin/artifact-plugins/ticket-summaries"


def deploy_plugin(source: Path, directory: Path, scope_ids: list[str] | None, **parameters: Any) -> Path:
    """Prepare a deployed version outside the discovery directory."""

    shutil.copytree(source, directory, ignore=shutil.ignore_patterns("__pycache__"))
    path = directory / "plugin.yml"
    manifest = yaml.safe_load(path.read_text())
    manifest.update(parameters)
    if scope_ids is None:
        manifest.pop("scope_ids", None)
    else:
        manifest["scope_ids"] = scope_ids
    path.write_text(yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return directory


def publish_plugin(directory: Path, plugin: Path, binding_name: str | None = None) -> Path:
    """Switch a user's discovery entry without changing any previous version."""

    if binding_name is None:
        binding_name = yaml.safe_load((plugin / "plugin.yml").read_text())["binding_name"]
    root = directory / "plugins"
    root.mkdir(exist_ok=True)
    entry = root / binding_name
    entry.unlink(missing_ok=True)
    entry.symlink_to(plugin, target_is_directory=True)
    return entry


async def reload_plugin(
    client: httpx.AsyncClient,
    directory: Path,
    plugin: Path,
    generation: int,
    phase: str = "disabled",
    registration: str = REGISTRATION,
) -> dict[str, Any]:
    publish_plugin(directory, plugin, registration.rsplit("/", 1)[1])
    response = await client.post(f"{registration}/reload", json={"expected_generation": generation})
    assert response.status_code == 202, response.text
    assert response.headers["location"] == registration
    return await plugin_state(client, phase, registration)


async def enable_all(
    client: httpx.AsyncClient, *, expected_failures: frozenset[str] = frozenset()
) -> dict[str, dict[str, Any]]:
    response = await client.post("/v1/admin/artifact-plugins/enable-all")
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    failed = frozenset(item["binding_name"] for item in items if item["outcome"] == "failed")
    assert failed == expected_failures, response.text
    assert all(item["status_url"] == f"/v1/admin/artifact-plugins/{item['binding_name']}" for item in items)
    return {item["binding_name"]: item for item in items}


@asynccontextmanager
async def running_server(
    directory: Path,
    *,
    envfile: Path | None = None,
    directory_from_env: bool = True,
    database_url: str | None = None,
    database_settings: dict[str, Any] | None = None,
):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = {key: value for key, value in os.environ.items() if not key.startswith("POWERCONTEXT_SERVER_")}
    env.update({
        "POWERCONTEXT_SERVER_DATABASE": json.dumps(
            database_settings
            or {
                "kind": "sqlite",
                "url": database_url or f"sqlite+aiosqlite:///{directory / 'server.db'}",
            }
        ),
        "POWERCONTEXT_SERVER_RUNTIME": json.dumps({"artifact_processing_families": []}),
        "POWERCONTEXT_SERVER_WORKSPACE": str(directory),
        "POWERCONTEXT_HOME": str(directory / "home"),
        "POWERCONTEXT_SERVER_AUTH_ENABLED": "true",
        "POWERCONTEXT_SERVER_AUTH_TOKEN": ADMIN_AUTHORIZATION_VALUE,
        "POWERCONTEXT_SERVER_ACCESS_MODE": "enforced",
        "POWERCONTEXT_SERVER_MCP_ENABLED": "false",
        "POWERCONTEXT_SERVER_METRICS_ENABLED": "true",
        "POWERCONTEXT_SERVER_DASHBOARD_ENABLED": "false",
    })
    (directory / "plugins").mkdir(exist_ok=True)
    if directory_from_env:
        env["POWERCONTEXT_SERVER_ARTIFACT_PLUGINS_DIR"] = str(directory / "plugins")
    envfile_args = ["--no-env-file"] if envfile is None else ["--env-file", str(envfile)]
    entrypoint = Path(sys.executable).with_name("powercontext")
    with (directory / "server.log").open("a") as log:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            str(entrypoint),
            "server",
            "run",
            *envfile_args,
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            cwd=directory,
            env=env,
            stdout=log,
            stderr=log,
        )
        try:
            async with httpx.AsyncClient(
                base_url=f"http://127.0.0.1:{port}",
                headers={"Authorization": f"Bearer {ADMIN_AUTHORIZATION_VALUE}"},
                timeout=20,
            ) as client:
                async with asyncio.timeout(40):
                    while True:
                        assert process.returncode is None, (directory / "server.log").read_text()
                        try:
                            response = await client.get("/health/ready")
                            if response.status_code == 200:
                                break
                        except httpx.ConnectError:
                            pass
                        await asyncio.sleep(0.1)
                yield client
        finally:
            if process.returncode is None:
                process.terminate()
            try:
                await asyncio.wait_for(process.wait(), 20)
            except TimeoutError:
                process.kill()
                await process.wait()


async def create_scope(client: httpx.AsyncClient, key: str) -> str:
    response = await client.post(
        "/v1/scopes", json={"title": key, "summary": "Artifact plugin acceptance", "idempotency_key": key}
    )
    assert response.status_code == 201, response.text
    return response.json()["scope_id"]


async def capture(client: httpx.AsyncClient, scope: str, source_id: str) -> None:
    response = await client.post(
        "/v1/sources/content",
        json={
            "scope_id": scope,
            "source_id": source_id,
            "content": f"Outage {source_id}: database recovered after retry.",
        },
    )
    assert response.status_code == 202, response.text


async def plugin_state(client: httpx.AsyncClient, phase: str, registration: str = REGISTRATION) -> dict[str, Any]:
    async with asyncio.timeout(40):
        while True:
            response = await client.get(registration)
            assert response.status_code == 200, response.text
            state = response.json()
            assert state["phase"] != "failed" or phase == "failed", state
            if state["phase"] == phase:
                return state
            await asyncio.sleep(0.1)


async def artifacts(
    client: httpx.AsyncClient, scope: str, count: int, family: str = "incident-report"
) -> list[dict[str, Any]]:
    async with asyncio.timeout(45):
        while True:
            response = await client.get(f"/v1/scopes/{scope}/artifacts/{family}")
            assert response.status_code == 200, response.text
            items = response.json()["items"]
            if len(items) == count:
                return items
            await asyncio.sleep(0.2)


async def query(client: httpx.AsyncClient, scope: str, family: str = "incident-report") -> dict[str, Any]:
    response = await client.post(f"/v1/scopes/{scope}/artifacts/{family}/search", json={"query": "Outage"})
    assert response.status_code == 200, response.text
    return response.json()


async def _wait_for_search_ready(client, scope, query):
    async with asyncio.timeout(30):
        while True:
            response = await client.post(f"/v1/scopes/{scope}/artifacts/incident-report/search", json={"query": query})
            if response.status_code != 503:
                assert response.status_code == 200, response.text
                return
            assert response.json()["error"]["code"] == "artifact_search_unavailable", response.text
            await asyncio.sleep(0.1)


async def change(
    client: httpx.AsyncClient, action: str, generation: int, phase: str, registration: str = REGISTRATION
) -> dict[str, Any]:
    response = await client.post(f"{registration}/{action}", json={"expected_generation": generation})
    assert response.status_code == 202, response.text
    assert response.headers["location"] == registration
    return await plugin_state(client, phase, registration)


async def wait_for_worker_metric(client: httpx.AsyncClient, metric: str, family: str = "incident-report") -> None:
    async with asyncio.timeout(40):
        while True:
            response = await client.get("/metrics")
            assert response.status_code == 200, response.text
            if any(
                f'family="{family}"' in line and float(line.rsplit(" ", 1)[1]) > 0
                for line in response.text.splitlines()
                if line.startswith(f"powercontext_server_artifact_processing_{metric}{{")
            ):
                return
            await asyncio.sleep(0.2)


async def worker_metric(client: httpx.AsyncClient, metric: str, family: str) -> float:
    response = await client.get("/metrics")
    assert response.status_code == 200, response.text
    values = [
        float(line.rsplit(" ", 1)[1])
        for line in response.text.splitlines()
        if line.startswith(f'powercontext_server_artifact_processing_{metric}{{family="{family}"}} ')
    ]
    assert len(values) == 1, response.text
    return values[0]


async def contents(client: httpx.AsyncClient, scope: str, family: str, count: int) -> list[dict[str, Any]]:
    result = []
    for item in await artifacts(client, scope, count, family):
        response = await client.get(f"/v1/scopes/{scope}/artifacts/{family}/{item['artifact_id']}")
        assert response.status_code == 200, response.text
        result.append(response.json())
    return result


def test_strict_plugin_configuration_survives_generation_reload_and_restart(tmp_path: Path) -> None:
    asyncio.run(_use_strict_plugin_configuration(tmp_path))


async def _use_strict_plugin_configuration(tmp_path: Path) -> None:
    plugin = deploy_plugin(EXAMPLE, tmp_path / "strict-v1", None)
    entry = plugin / "plugin.py"
    entry.write_text(
        entry
        .read_text()
        .replace("import hashlib", "import hashlib\nfrom datetime import date\nfrom enum import Enum")
        .replace('ConfigDict(extra="forbid", frozen=True)', 'ConfigDict(extra="forbid", frozen=True, strict=True)')
        .replace(
            "class Config(BaseModel):",
            'class Mode(str, Enum):\n    NORMAL = "normal"\n    URGENT = "urgent"\n\n\nclass Config(BaseModel):',
        )
        .replace(
            '    title_prefix: str = "Incident report"',
            '    mode: Mode = Mode.NORMAL\n    observed_at: date = date(2026, 10, 10)\n    labels: tuple[str, ...] = ("初始",)\n    title_prefix: str = "Incident report"',
        )
        .replace(
            "    return IncidentReportPipeline(config)",
            '    prefix = f"{config.mode.value}/{config.observed_at.isoformat()}/{config.labels}"\n    return IncidentReportPipeline(config.model_copy(update={"title_prefix": prefix}))',
        )
    )
    async with running_server(tmp_path) as client:
        scope = await create_scope(client, "strict-configuration")
        await reload_plugin(client, tmp_path, plugin, 0)
        await change(client, "enable", 1, "enabled")
        await capture(client, scope, "before-restart")
        reports = await contents(client, scope, "incident-report", 1)
        assert reports[0]["content"]["title"].startswith("normal/2026-10-10/('初始',):")
    async with running_server(tmp_path) as client:
        state = await plugin_state(client, "enabled")
        await capture(client, scope, "after-restart")
        reports = await contents(client, scope, "incident-report", 2)
        assert all(item["content"]["title"].startswith("normal/2026-10-10/('初始',):") for item in reports)
        replacement = deploy_plugin(
            plugin,
            tmp_path / "strict-v2",
            None,
            version="2",
            config={"mode": "urgent", "observed_at": "2026-10-11", "labels": ["更新"]},
        )
        await reload_plugin(client, tmp_path, replacement, state["binding_generation"], "enabled")
        await capture(client, scope, "after-reload")
        reports = await contents(client, scope, "incident-report", 3)
        assert next(item for item in reports if item["sources"][0]["source_id"] == "after-reload")["content"][
            "title"
        ].startswith("urgent/2026-10-11/('更新',):")


def test_plugin_cannot_register_the_builtin_atomic_memory_family(tmp_path: Path) -> None:
    asyncio.run(_reject_builtin_atomic_memory_plugin(tmp_path))


async def _reject_builtin_atomic_memory_plugin(tmp_path: Path) -> None:
    plugin = deploy_plugin(EXAMPLE, tmp_path / "builtin-family", None)
    entry = plugin / "plugin.py"
    entry.write_text(entry.read_text().replace('"incident-report"', '"atomic-memory"'))
    async with running_server(tmp_path) as client:
        publish_plugin(tmp_path, plugin)
        response = await client.post(f"{REGISTRATION}/reload", json={"expected_generation": 0})
        assert response.status_code == 422, response.text
        response = await client.get("/v1/admin/artifact-plugins")
        assert response.status_code == 200, response.text
        assert response.json()["items"] == []


def test_in_memory_database_rejects_plugin_enable_without_changing_registration(tmp_path: Path) -> None:
    asyncio.run(_reject_in_memory_plugin_generation(tmp_path))


async def _reject_in_memory_plugin_generation(tmp_path: Path) -> None:
    plugin = deploy_plugin(EXAMPLE, tmp_path / "memory-plugin", None)
    publish_plugin(tmp_path, plugin)
    async with running_server(tmp_path, database_url="sqlite+aiosqlite:///:memory:") as client:
        state = await plugin_state(client, "disabled")
        scope = await create_scope(client, "in-memory-input")
        await capture(client, scope, "pending-input")
        response = await client.post(
            f"{REGISTRATION}/enable", json={"expected_generation": state["binding_generation"]}
        )
        assert response.status_code == 422, response.text
        assert await plugin_state(client, "disabled") == state
        item = (await enable_all(client, expected_failures=frozenset({"incident-reports"})))["incident-reports"]
        assert item["outcome"] == "failed"
        assert item["error"] == "invalid_plugin"
        assert item["state"] == state
        assert await artifacts(client, scope, 0) == []


def test_plugin_manifests_reject_enable_fields_without_replacing_saved_definition(tmp_path: Path) -> None:
    asyncio.run(_reject_manifest_enable_fields(tmp_path))


async def _reject_manifest_enable_fields(tmp_path: Path) -> None:
    report = deploy_plugin(EXAMPLE, tmp_path / "report-v1", None, config={"title_prefix": "Saved"})
    publish_plugin(tmp_path, report)
    invalid_registrations = []
    for field in ("enable", "enabled"):
        binding_name = f"invalid-{field}"
        plugin = deploy_plugin(
            SUMMARY_EXAMPLE,
            tmp_path / binding_name,
            None,
            binding_name=binding_name,
            **{field: True},
        )
        publish_plugin(tmp_path, plugin)
        invalid_registrations.append((field, binding_name))

    async with running_server(tmp_path) as client:
        state = await plugin_state(client, "disabled")
        response = await client.get("/v1/admin/artifact-plugins")
        assert response.status_code == 200, response.text
        assert response.json()["items"] == [state]
        for field, binding_name in invalid_registrations:
            registration = f"/v1/admin/artifact-plugins/{binding_name}"
            assert (await client.get(registration)).status_code == 404
            for value in (True, False):
                candidate = deploy_plugin(
                    SUMMARY_EXAMPLE,
                    tmp_path / f"{binding_name}-{value}",
                    None,
                    binding_name=binding_name,
                    **{field: value},
                )
                publish_plugin(tmp_path, candidate)
                rejected = await client.post(f"{registration}/reload", json={"expected_generation": 0})
                assert rejected.status_code == 422, rejected.text
                assert (await client.get(registration)).status_code == 404

        assert set(await enable_all(client)) == {"incident-reports"}
        state = await plugin_state(client, "enabled")
        scope = await create_scope(client, "manifest-validation")
        await capture(client, scope, "before-invalid-update")
        await artifacts(client, scope, 1)
        for field in ("enable", "enabled"):
            for value in (True, False):
                candidate = deploy_plugin(
                    EXAMPLE,
                    tmp_path / f"report-{field}-{value}",
                    None,
                    config={"title_prefix": "Rejected"},
                    **{field: value},
                )
                publish_plugin(tmp_path, candidate)
                rejected = await client.post(
                    f"{REGISTRATION}/reload", json={"expected_generation": state["binding_generation"]}
                )
                assert rejected.status_code == 422, rejected.text
                assert await plugin_state(client, "enabled") == state
        await capture(client, scope, "after-invalid-update")
        results = await contents(client, scope, "incident-report", 2)
        assert all(item["content"]["title"].startswith("Saved:") for item in results)


def test_user_plugin_through_server_hot_management_and_restart(tmp_path: Path) -> None:
    asyncio.run(_use_plugin_through_server(tmp_path))


async def _use_plugin_through_server(tmp_path: Path) -> None:
    # Existing Scopes and inputs are created through the delivered HTTP API.
    async with running_server(tmp_path) as client:
        scope = await create_scope(client, "reports")
        other_scope = await create_scope(client, "other")
        await capture(client, scope, "ticket-1")
    plugin = deploy_plugin(EXAMPLE, tmp_path / "incident-report-v1", [scope], config={"title_prefix": "Report"})
    factory_signal = tmp_path / "pipeline-created"
    entry = plugin / "plugin.py"
    entry.write_text(
        entry.read_text().replace(
            "    return IncidentReportPipeline(config)",
            f"    from pathlib import Path\n    Path({str(factory_signal)!r}).write_text('created')\n    return IncidentReportPipeline(config)",
        )
    )
    publish_plugin(tmp_path, plugin)
    broken = deploy_plugin(
        SUMMARY_EXAMPLE, tmp_path / "broken-v1", [scope], binding_name="broken-summary", config={"unknown": True}
    )
    publish_plugin(tmp_path, broken)
    unregistered = deploy_plugin(SUMMARY_EXAMPLE, tmp_path / "unregistered-plugin", [scope])
    # Neither version directories outside the root nor nested entries are discovered.
    nested = tmp_path / "plugins" / "nested"
    nested.mkdir()
    (nested / "ticket-summaries").symlink_to(unregistered, target_is_directory=True)
    envfile = tmp_path / "server.env"
    envfile.write_text(f"POWERCONTEXT_SERVER_ARTIFACT_PLUGINS_DIR={tmp_path / 'plugins'}\n")
    async with running_server(tmp_path, envfile=envfile, directory_from_env=False) as client:
        state = await plugin_state(client, "disabled")
        assert state["binding_generation"] == 1
        registrations = (await client.get("/v1/admin/artifact-plugins")).json()["items"]
        assert [item["binding_name"] for item in registrations] == ["incident-reports"]
        await asyncio.sleep(2)
        assert not factory_signal.exists(), "Loading a declaration must not construct the generation pipeline"
        assert await artifacts(client, scope, 0) == []
        metadata = (await client.get("/v1/capabilities")).json()
        assert "incident-report" in metadata["artifact_families"]
        schema = next(item for item in metadata["custom_artifact_schemas"] if item["family"] == "incident-report")
        assert schema["schema_version"] == "1"
        assert schema["search"]["indexes"][0]["fields"] == ["title", "evidence_text"]
        denied = await client.post("/v1/admin/artifact-plugins/enable-all", headers={"Authorization": ""})
        assert denied.status_code == 401
        obsolete = await client.post(
            f"{REGISTRATION}/reload",
            json={
                "expected_generation": 1,
                "directory": str(plugin),
                "scope_ids": [scope],
                "config": {},
                "enabled": True,
            },
        )
        assert obsolete.status_code == 422, obsolete.text
        assert (await client.put(REGISTRATION, json={"expected_generation": 1})).status_code == 405
        accepted = await enable_all(client)
        assert accepted["incident-reports"]["outcome"] == "accepted"
        state = await plugin_state(client, "enabled")
        assert state["binding_generation"] == 2
        await artifacts(client, scope, 1)
        assert factory_signal.exists()
        assert (await enable_all(client))["incident-reports"]["outcome"] == "skipped"
        await capture(client, scope, "ticket-2")
        await capture(client, other_scope, "outside-scope")
        items = await artifacts(client, scope, 2)
        assert {item["revision"] for item in items} == {1}
        page = await client.get(f"/v1/scopes/{scope}/artifacts/incident-report", params={"limit": 1})
        assert page.status_code == 200, page.text
        next_page = await client.get(
            f"/v1/scopes/{scope}/artifacts/incident-report", params={"limit": 1, "cursor": page.json()["next_cursor"]}
        )
        assert next_page.status_code == 200, next_page.text
        assert {page.json()["items"][0]["artifact_id"], next_page.json()["items"][0]["artifact_id"]} == {
            item["artifact_id"] for item in items
        }
        for item in items:
            result = await client.get(f"/v1/scopes/{scope}/artifacts/incident-report/{item['artifact_id']}")
            assert result.status_code == 200, result.text
            artifact = result.json()
            assert artifact["schema_version"] == "1"
            assert artifact["content"]["title"].startswith("Report: ticket-")
            assert artifact["content"]["evidence_text"].startswith("Outage ticket-")
            assert artifact["sources"] == [
                {"source_type": "content", "source_id": artifact["content"]["title"].split(": ")[1]}
            ]
            cached = await client.get(
                f"/v1/scopes/{scope}/artifacts/incident-report/{item['artifact_id']}",
                headers={"If-None-Match": result.headers["etag"]},
            )
            assert cached.status_code == 304
        assert len((await query(client, scope))["results"]) == 2
        assert (await query(client, other_scope))["results"] == []
        await capture(client, scope, "ticket-1")
        stale = await client.post(f"{REGISTRATION}/disable", json={"expected_generation": 99})
        assert stale.status_code == 409
        state = await change(client, "disable", 2, "disabled")
        await capture(client, scope, "ticket-3")
        assert len((await query(client, scope))["results"]) == 2
        assert "enabled" not in yaml.safe_load((plugin / "plugin.yml").read_text())
        replacement = deploy_plugin(
            EXAMPLE, tmp_path / "incident-report-v2", [scope], version="2", config={"title_prefix": "Replacement"}
        )
        publish_plugin(tmp_path, replacement)
    # Environment variables take priority over an envfile. Scanning cannot reset a saved disable or update its code.
    envfile.write_text(f"POWERCONTEXT_SERVER_ARTIFACT_PLUGINS_DIR={tmp_path / 'wrong-discovery-root'}\n")
    async with running_server(tmp_path, envfile=envfile) as client:
        state = await plugin_state(client, "disabled")
        assert state["binding_generation"] == 3
        assert len((await query(client, scope))["results"]) == 2
        assert len((await client.get("/v1/admin/artifact-plugins")).json()["items"]) == 1
        state = await change(client, "enable", state["binding_generation"], "enabled")
        assert state["binding_generation"] == 4
        results = await contents(client, scope, "incident-report", 3)
        assert all(item["content"]["title"].startswith("Report:") for item in results)
    # Enabled registrations resume without another batch enable and keep the resolved saved version.
    async with running_server(tmp_path) as client:
        state = await plugin_state(client, "enabled")
        assert state["binding_generation"] > 4
        await capture(client, scope, "ticket-4")
        results = await contents(client, scope, "incident-report", 4)
        assert all(item["content"]["title"].startswith("Report:") for item in results)
        # Explicit reload picks up the switched entry and keeps this plugin enabled.
        state = await reload_plugin(client, tmp_path, replacement, state["binding_generation"], "enabled")
        unchanged = await reload_plugin(client, tmp_path, replacement, state["binding_generation"], "enabled")
        assert unchanged["binding_generation"] == state["binding_generation"]
        unloaded = await change(client, "unload", state["binding_generation"], "unloaded")
        shutil.rmtree(plugin)
    # Startup must not load even a valid discovery entry for an unloaded registration.
    async with running_server(tmp_path) as client:
        state = await plugin_state(client, "unloaded")
        assert state["binding_generation"] == unloaded["binding_generation"]
        assert (await enable_all(client))["incident-reports"]["outcome"] == "skipped"
        assert len((await query(client, scope))["results"]) == 4
        await capture(client, scope, "ticket-5")
        assert len(await artifacts(client, scope, 4)) == 4
        for item in results:
            result = await client.get(f"/v1/scopes/{scope}/artifacts/incident-report/{item['artifact_id']}")
            assert result.status_code == 200, result.text
        state = await reload_plugin(client, tmp_path, replacement, state["binding_generation"])
        assert len((await query(client, scope))["results"]) == 4
        await change(client, "enable", state["binding_generation"], "enabled")
        results = await contents(client, scope, "incident-report", 5)
        assert (
            next(item for item in results if item["sources"][0]["source_id"] == "ticket-5")["content"]["title"]
            == "Replacement: ticket-5"
        )


def test_generation_failure_retries_uncommitted_inputs_after_config_update(tmp_path: Path) -> None:
    asyncio.run(_recover_generation_failure(tmp_path))


async def _recover_generation_failure(tmp_path: Path) -> None:
    code = tmp_path / "user-plugin-code"
    shutil.copytree(EXAMPLE, code, ignore=shutil.ignore_patterns("__pycache__"))
    entry = code / "plugin.py"
    entry.write_text(
        entry
        .read_text()
        .replace(
            '    title_prefix: str = "Incident report"',
            '    deployment_label: str = Field(alias="label")\n    title_prefix: str = "Incident report"',
        )
        .replace(
            "    return IncidentReportPipeline(config)",
            '    if config.deployment_label != "recovery":\n        raise ValueError("Invalid deployment label")\n    return IncidentReportPipeline(config)',
        )
    )
    async with running_server(tmp_path) as client:
        scope = await create_scope(client, "reports")
        replacement_scope = await create_scope(client, "replacement")
        plugin = deploy_plugin(
            code, tmp_path / "plugin-v1", [scope], config={"fail_on_text": "Outage", "label": "recovery"}
        )
        await capture(client, scope, "failed-ticket")
        await reload_plugin(client, tmp_path, plugin, 0)
        await change(client, "enable", 1, "enabled")
        # The product exposes Worker failures through its authenticated metrics.
        await wait_for_worker_metric(client, "failed_total")
        assert await artifacts(client, scope, 0) == []
        invalid_directory = deploy_plugin(plugin, tmp_path / "invalid-config", [scope], config={"unknown": True})
        publish_plugin(tmp_path, invalid_directory, "incident-reports")
        invalid = await client.post(f"{REGISTRATION}/reload", json={"expected_generation": 2})
        assert invalid.status_code == 422, invalid.text
        assert (await plugin_state(client, "enabled"))["binding_generation"] == 2
        invalid_entry = deploy_plugin(plugin, tmp_path / "invalid-entry", [scope])
        entry = invalid_entry / "plugin.py"
        entry.write_text(
            entry.read_text().replace(
                "async def create_pipeline(ctx: GenerationContext, config: Config)",
                "async def create_pipeline(config: Config)",
            )
        )
        publish_plugin(tmp_path, invalid_entry, "incident-reports")
        invalid = await client.post(f"{REGISTRATION}/reload", json={"expected_generation": 2})
        assert invalid.status_code == 422, invalid.text
        assert (await plugin_state(client, "enabled"))["binding_generation"] == 2
        mismatch = deploy_plugin(plugin, tmp_path / "wrong-binding", [scope], binding_name="another-binding")
        publish_plugin(tmp_path, mismatch, "incident-reports")
        invalid = await client.post(f"{REGISTRATION}/reload", json={"expected_generation": 2})
        assert invalid.status_code == 422, invalid.text
        assert (await plugin_state(client, "enabled"))["binding_generation"] == 2
        incompatible = deploy_plugin(plugin, tmp_path / "incompatible-schema", [scope])
        entry = incompatible / "plugin.py"
        entry.write_text(entry.read_text().replace("Field(min_length=1)", "Field(min_length=2)"))
        publish_plugin(tmp_path, incompatible, "incident-reports")
        conflict = await client.post(f"{REGISTRATION}/reload", json={"expected_generation": 2})
        assert conflict.status_code == 409, conflict.text
        assert (await plugin_state(client, "enabled"))["binding_generation"] == 2
        replacement = deploy_plugin(
            plugin, tmp_path / "plugin-v2", [replacement_scope], version="2", config={"label": "recovery"}
        )
        await reload_plugin(client, tmp_path, replacement, 2, "enabled")
        await capture(client, replacement_scope, "replacement-ticket")
        await artifacts(client, replacement_scope, 1)
        await wait_for_worker_metric(client, "completed_total")
        assert await artifacts(client, scope, 0) == []
        metrics = await client.get("/metrics")
        assert metrics.status_code == 200, metrics.text
        failures = [
            float(line.rsplit(" ", 1)[1])
            for line in metrics.text.splitlines()
            if line.startswith('powercontext_server_artifact_processing_failed_total{family="incident-report"}')
        ]
        assert failures == [0.0], "Removed Scopes must not keep dispatching failed Workers"
        # Re-adding a Scope resumes its retained input instead of losing the failed window.
        restored = deploy_plugin(
            replacement, tmp_path / "plugin-v3", [scope, replacement_scope], version="3", config={"label": "recovery"}
        )
        await reload_plugin(client, tmp_path, restored, 3, "enabled")
        items = await artifacts(client, scope, 1)
        assert items[0]["revision"] == 1
        # First publication is asynchronous even after generation has committed.
        await _wait_for_search_ready(client, scope, "Outage")
        assert len((await query(client, scope))["results"]) == 1


def test_disabling_an_active_worker_reclaims_it_before_reenabling(tmp_path: Path) -> None:
    asyncio.run(_disable_active_generation(tmp_path))


async def _disable_active_generation(tmp_path: Path) -> None:
    code = tmp_path / "user-plugin-code"
    shutil.copytree(EXAMPLE, code, ignore=shutil.ignore_patterns("__pycache__"))
    entry = code / "plugin.py"
    entry.write_text(
        entry
        .read_text()
        .replace("import hashlib", "import asyncio\nimport hashlib")
        .replace(
            '    title_prefix: str = "Incident report"',
            '    block_generation: bool = False\n    title_prefix: str = "Incident report"',
        )
        + """

class SummaryPipeline:
    def __init__(self, ctx, config):
        self.ctx = ctx
        self.config = config

    async def generate(self, batch):
        if self.config.block_generation:
            await asyncio.Future()
        previous = await self.ctx.read_latest("incident-report", "latest-report")
        old_text = "" if previous is None else previous.content["evidence_text"] + "\\n"
        return [ArtifactDraft(
            family="incident-report", schema_version="1", artifact_id="latest-report",
            expected_revision=0 if previous is None else previous.ref.revision,
            content={"title": self.config.title_prefix,
                     "evidence_text": old_text + "\\n".join(TextEvidence.model_validate(source.value).content for source in batch.sources)},
            source_refs=tuple(source.ref for source in batch.sources),
            artifact_refs=() if previous is None else (previous.ref,),
        )]

async def create_pipeline(ctx, config):
    return SummaryPipeline(ctx, config)
"""
    )
    async with running_server(tmp_path) as client:
        scope = await create_scope(client, "reports")
        plugin = deploy_plugin(code, tmp_path / "plugin-v1", [scope], config={"block_generation": True})
        await capture(client, scope, "active-ticket")
        await reload_plugin(client, tmp_path, plugin, 0)
        await change(client, "enable", 1, "enabled")
        await wait_for_worker_metric(client, "used_workers")
        await change(client, "disable", 2, "disabled")
        assert await artifacts(client, scope, 0) == []
        replacement = deploy_plugin(
            plugin,
            tmp_path / "plugin-v2",
            [scope],
            version="2",
            config={"block_generation": False, "title_prefix": "Replacement"},
        )
        state = await reload_plugin(client, tmp_path, replacement, 3)
        assert await artifacts(client, scope, 0) == []
        await change(client, "enable", state["binding_generation"], "enabled")
        items = await artifacts(client, scope, 1)
        response = await client.get(f"/v1/scopes/{scope}/artifacts/incident-report/{items[0]['artifact_id']}")
        assert response.status_code == 200, response.text
        assert response.json()["revision"] == 1
        assert response.json()["content"]["title"] == "Replacement"
        await capture(client, scope, "update-ticket")
        async with asyncio.timeout(45):
            while True:
                response = await client.get(f"/v1/scopes/{scope}/artifacts/incident-report/latest-report")
                assert response.status_code == 200, response.text
                if response.json()["revision"] == 2:
                    break
                await asyncio.sleep(0.2)
        result = response.json()
        assert "active-ticket" in result["content"]["evidence_text"]
        assert "update-ticket" in result["content"]["evidence_text"]
        assert result["sources"] == [{"source_type": "content", "source_id": "update-ticket"}]
        assert result["artifacts"] == [{"family": "incident-report", "artifact_id": "latest-report", "revision": 1}]
        assert (await query(client, scope))["results"][0]["revision"] == 2
        previous = await client.get(f"/v1/scopes/{scope}/artifacts/incident-report/latest-report/revisions/1")
        assert previous.status_code == 200, previous.text
        assert "update-ticket" not in previous.json()["content"]["evidence_text"]


def test_multiple_plugin_manifests_apply_independent_configuration(tmp_path: Path) -> None:
    asyncio.run(_use_independent_plugins(tmp_path))


async def _use_independent_plugins(tmp_path: Path) -> None:
    summary_registration = "/v1/admin/artifact-plugins/ticket-summaries"
    async with running_server(tmp_path) as client:
        report_scope = await create_scope(client, "reports")
        other_report_scope = await create_scope(client, "other-reports")
        summary_scope = await create_scope(client, "summaries")
        report = deploy_plugin(
            EXAMPLE,
            tmp_path / "report-v1",
            [report_scope, other_report_scope],
            max_sources=1,
            processing={"interval_seconds": 0.5, "max_workers": 2, "timeout_seconds": 60},
            config={"title_prefix": "独立报告"},
        )
        # This user pipeline accepts a single evidence item per invocation.
        entry = report / "plugin.py"
        entry.write_text(
            entry.read_text().replace(
                "        drafts = []",
                '        if len(batch.sources) != 1:\n            raise ValueError("Expected one ticket per report invocation")\n        drafts = []',
            )
        )
        summary = deploy_plugin(
            SUMMARY_EXAMPLE,
            tmp_path / "summary-v1",
            [summary_scope],
            max_sources=2,
            processing={"interval_seconds": 15, "max_workers": 1, "timeout_seconds": 90},
            config={"language": "Français"},
        )
        for scope in (report_scope, summary_scope):
            for number in range(3):
                await capture(client, scope, f"ticket-{number}")
        await capture(client, other_report_scope, "another-ticket")
        assert (await client.get("/v1/admin/artifact-plugins")).json()["items"] == []
        for registration, directory in ((REGISTRATION, report), (summary_registration, summary)):
            await reload_plugin(client, tmp_path, directory, 0, registration=registration)
        assert await artifacts(client, report_scope, 0) == []
        assert await artifacts(client, summary_scope, 0, "ticket-summary") == []
        # A temporarily unavailable deployed version fails independently; successful items stay accepted.
        unavailable = summary.with_name("summary-v1-unavailable")
        summary.rename(unavailable)
        results = await enable_all(client, expected_failures=frozenset({"ticket-summaries"}))
        assert results["incident-reports"]["outcome"] == "accepted"
        assert results["ticket-summaries"]["outcome"] == "failed"
        assert results["ticket-summaries"]["error"] == "invalid_plugin"
        assert (await plugin_state(client, "disabled", summary_registration))["binding_generation"] == 1
        await plugin_state(client, "enabled")
        summary = unavailable.rename(summary)
        results = await enable_all(client)
        assert results["incident-reports"]["outcome"] == "skipped"
        assert results["ticket-summaries"]["outcome"] == "accepted"
        await plugin_state(client, "enabled", summary_registration)
        assert all(item["outcome"] == "skipped" for item in (await enable_all(client)).values())
        started = asyncio.get_running_loop().time()
        assert await worker_metric(client, "max_workers", "incident-report") == 2
        assert await worker_metric(client, "max_workers", "ticket-summary") == 1
        # No second automatic window may be admitted before this binding's interval.
        # The first window can run immediately and must contain at most two Sources.
        while asyncio.get_running_loop().time() < started + 14:
            response = await client.get(f"/v1/scopes/{summary_scope}/artifacts/ticket-summary")
            assert response.status_code == 200, response.text
            assert len(response.json()["items"]) <= 1, "The summary binding ignored its 15-second interval"
            await asyncio.sleep(0.2)
        reports = await contents(client, report_scope, "incident-report", 3)
        assert all(item["content"]["title"].startswith("独立报告:") for item in reports)
        assert all(len(item["sources"]) == 1 for item in reports)
        await artifacts(client, other_report_scope, 1)
        summaries = await contents(client, summary_scope, "ticket-summary", 2)
        assert sorted(len(item["sources"]) for item in summaries) == [1, 2]
        assert all(item["content"]["language"] == "Français" for item in summaries)
        assert {source["source_id"] for item in summaries for source in item["sources"]} == {
            "ticket-0",
            "ticket-1",
            "ticket-2",
        }
        assert (await query(client, report_scope, "ticket-summary"))["results"] == []
        assert (await query(client, summary_scope))["results"] == []
        assert "enabled" not in yaml.safe_load((report / "plugin.yml").read_text())
        assert "enabled" not in yaml.safe_load((summary / "plugin.yml").read_text())
        replacement = deploy_plugin(
            summary,
            tmp_path / "summary-v2",
            [summary_scope],
            version="2",
            max_sources=1,
            processing={"interval_seconds": 0.5, "max_workers": 2, "timeout_seconds": 60},
            config={"language": "Deutsch"},
        )
        await reload_plugin(client, tmp_path, replacement, 2, "enabled", summary_registration)
        for number in (3, 4):
            await capture(client, summary_scope, f"ticket-{number}")
        summaries = await contents(client, summary_scope, "ticket-summary", 4)
        updated = [item for item in summaries if item["content"]["language"] == "Deutsch"]
        assert len(updated) == 2
        assert all(len(item["sources"]) == 1 for item in updated)
        assert await worker_metric(client, "max_workers", "ticket-summary") == 2
        assert await worker_metric(client, "max_workers", "incident-report") == 2
        await change(client, "unload", 3, "unloaded", summary_registration)
        await capture(client, report_scope, "report-after-summary-unload")
        await artifacts(client, report_scope, 4)
        assert len((await query(client, summary_scope, "ticket-summary"))["results"]) == 4


def test_worker_timeout_is_local_to_its_plugin_and_code_update_recovers(tmp_path: Path) -> None:
    asyncio.run(_recover_timed_out_plugin(tmp_path))


async def _recover_timed_out_plugin(tmp_path: Path) -> None:
    summary_registration = "/v1/admin/artifact-plugins/ticket-summaries"
    async with running_server(tmp_path) as client:
        report_scope = await create_scope(client, "timed-out-reports")
        summary_scope = await create_scope(client, "healthy-summaries")
        blocked = deploy_plugin(
            EXAMPLE,
            tmp_path / "blocked-v1",
            [report_scope],
            processing={"interval_seconds": 1, "max_workers": 1, "timeout_seconds": 5},
        )
        entry = blocked / "plugin.py"
        entry.write_text(
            entry
            .read_text()
            .replace("import hashlib", "import asyncio\nimport hashlib")
            .replace("        drafts = []", "        await asyncio.Future()\n        drafts = []")
        )
        healthy = deploy_plugin(SUMMARY_EXAMPLE, tmp_path / "healthy-v1", [summary_scope])
        for registration, directory in ((REGISTRATION, blocked), (summary_registration, healthy)):
            await reload_plugin(client, tmp_path, directory, 0, registration=registration)
        assert all(item["outcome"] == "accepted" for item in (await enable_all(client)).values())
        await plugin_state(client, "enabled")
        await plugin_state(client, "enabled", summary_registration)
        await capture(client, report_scope, "timed-out-ticket")
        await capture(client, summary_scope, "healthy-ticket")
        await wait_for_worker_metric(client, "timeouts_total")
        assert await artifacts(client, report_scope, 0) == []
        healthy_result = await contents(client, summary_scope, "ticket-summary", 1)
        assert healthy_result[0]["content"]["source_ids"] == ["healthy-ticket"]
        assert (await plugin_state(client, "enabled", summary_registration))["binding_generation"] == 2
        recovered = deploy_plugin(
            EXAMPLE,
            tmp_path / "report-v2",
            [report_scope],
            version="2",
            config={"title_prefix": "Recovered"},
        )
        await reload_plugin(client, tmp_path, recovered, 2, "enabled")
        result = await contents(client, report_scope, "incident-report", 1)
        assert result[0]["revision"] == 1
        assert result[0]["content"]["title"] == "Recovered: timed-out-ticket"
        assert len((await query(client, summary_scope, "ticket-summary"))["results"]) == 1


def test_enable_all_fixes_its_snapshot_and_reports_concurrent_changes(tmp_path: Path) -> None:
    asyncio.run(_enable_all_with_concurrent_changes(tmp_path))


async def _enable_all_with_concurrent_changes(tmp_path: Path) -> None:
    async with running_server(tmp_path) as client:
        scope = await create_scope(client, "batch-management")
        report = deploy_plugin(EXAMPLE, tmp_path / "report-v1", [scope])
        gate = tmp_path / "validation-paused"
        entered = tmp_path / "validation-entered"
        entry = report / "plugin.py"
        # A user module with a slow import allows independent HTTP management during validation.
        entry.write_text(
            entry.read_text()
            + f"""

from pathlib import Path
import time
if Path({str(gate)!r}).exists():
    Path({str(entered)!r}).write_text("entered")
    while Path({str(gate)!r}).exists():
        time.sleep(0.05)
"""
        )
        await reload_plugin(client, tmp_path, report, 0)
        summary = deploy_plugin(SUMMARY_EXAMPLE, tmp_path / "summary-v1", [scope])
        publish_plugin(tmp_path, summary)
        # Merely publishing an entry after startup does not make it eligible for a batch enable.
        assert (await client.get(SUMMARY_REGISTRATION)).status_code == 404
        gate.touch()
        batch = asyncio.create_task(enable_all(client, expected_failures=frozenset({"incident-reports"})))
        try:
            async with asyncio.timeout(10):
                while True:
                    if entered.exists():
                        break
                    await asyncio.sleep(0.05)
            await reload_plugin(client, tmp_path, summary, 0, registration=SUMMARY_REGISTRATION)
            replacement = deploy_plugin(EXAMPLE, tmp_path / "report-v2", [scope], version="2")
            updated = await reload_plugin(client, tmp_path, replacement, 1)
            assert updated["binding_generation"] == 2
        finally:
            gate.unlink(missing_ok=True)
        results = await batch
        assert set(results) == {"incident-reports"}, "A batch must not include plugins loaded after its snapshot"
        assert results["incident-reports"]["outcome"] == "failed"
        assert results["incident-reports"]["error"] == "conflict"
        assert results["incident-reports"]["state"]["binding_generation"] == 2
        assert (await plugin_state(client, "disabled"))["binding_generation"] == 2
        assert (await plugin_state(client, "disabled", SUMMARY_REGISTRATION))["binding_generation"] == 1
        assert all(item["outcome"] == "accepted" for item in (await enable_all(client)).values())
        # Transitions and completed enables are both ineligible for an immediate repeat.
        assert all(item["outcome"] == "skipped" for item in (await enable_all(client)).values())
        await plugin_state(client, "enabled")
        await plugin_state(client, "enabled", SUMMARY_REGISTRATION)


def test_failed_restore_waits_for_explicit_reload_and_preserves_progress(tmp_path: Path) -> None:
    asyncio.run(_repair_failed_restore(tmp_path))


async def _repair_failed_restore(tmp_path: Path) -> None:
    async with running_server(tmp_path) as client:
        scope = await create_scope(client, "failed-restore")
        healthy_scope = await create_scope(client, "healthy")
        report = deploy_plugin(EXAMPLE, tmp_path / "report-v1", [scope])
        summary = deploy_plugin(SUMMARY_EXAMPLE, tmp_path / "summary-v1", [healthy_scope])
        await reload_plugin(client, tmp_path, report, 0)
        await change(client, "enable", 1, "enabled")
        await reload_plugin(client, tmp_path, summary, 0, registration=SUMMARY_REGISTRATION)
        await capture(client, scope, "before-failure")
        await artifacts(client, scope, 1)
    # An unavailable saved version must fail restoration even if the discovery entry points at a valid update.
    report.rename(tmp_path / "report-v1-unavailable")
    replacement = deploy_plugin(
        EXAMPLE, tmp_path / "report-v2", [scope], version="2", config={"title_prefix": "Repaired"}
    )
    publish_plugin(tmp_path, replacement)
    async with running_server(tmp_path) as client:
        failed = await plugin_state(client, "failed")
        assert failed["binding_generation"] == 3
        assert failed["last_error"]
        await capture(client, scope, "during-failure")
        results = await enable_all(client)
        assert results["incident-reports"]["outcome"] == "skipped"
        assert results["ticket-summaries"]["outcome"] == "accepted"
        await plugin_state(client, "enabled", SUMMARY_REGISTRATION)
        await capture(client, healthy_scope, "healthy-ticket")
        await artifacts(client, healthy_scope, 1, "ticket-summary")
        assert len((await query(client, scope))["results"]) == 1
    # Failed is durable, never auto-enabled by startup or by enable-all.
    async with running_server(tmp_path) as client:
        state = await plugin_state(client, "failed")
        assert state["binding_generation"] == failed["binding_generation"]
        assert all(item["outcome"] == "skipped" for item in (await enable_all(client)).values())
        invalid = await client.post(f"{REGISTRATION}/enable", json={"expected_generation": state["binding_generation"]})
        assert invalid.status_code == 422, invalid.text
        state = await reload_plugin(client, tmp_path, replacement, state["binding_generation"])
        assert await artifacts(client, scope, 1)
        assert (await enable_all(client))["incident-reports"]["outcome"] == "accepted"
        await plugin_state(client, "enabled")
        results = await contents(client, scope, "incident-report", 2)
        repaired = next(item for item in results if item["sources"][0]["source_id"] == "during-failure")
        assert repaired["revision"] == 1
        assert repaired["content"]["title"] == "Repaired: during-failure"
        assert len((await query(client, healthy_scope, "ticket-summary"))["results"]) == 1
