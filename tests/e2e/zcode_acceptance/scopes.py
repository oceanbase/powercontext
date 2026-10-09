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

"""Scope priority and identity isolation through actual host prompts and public binding APIs."""

from __future__ import annotations

import json
import shutil
from hashlib import sha256
from typing import TYPE_CHECKING

from pydantic import SecretStr

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime.config import InferenceConfig, RuntimeConfig
from powercontext.server.factory import create_server_app
from powercontext.server.settings import BearerAuthConfig, ServerSettings

from .protocol import ObserveServer

if TYPE_CHECKING:
    from .runner import AcceptanceRun


def scope_isolation(run: AcceptanceRun) -> None:
    with run.scenario(4) as evidence:
        original_environment = dict(run.environment)
        original_workspace = run.workspace
        ids = [
            run.post(
                "/v1/scopes",
                {
                    "title": f"Synthetic {kind} {run.run_id}",
                    "summary": "Disposable binding test",
                    "idempotency_key": kind + run.run_id,
                },
            )["scope_id"]
            for kind in ("workspace", "session")
        ]
        workspace_scope, session_scope = ids
        # Query the plugin's canonical workspace algorithm rather than assuming the checkout root.
        key_process = run.process([
            str(run.node),
            "--input-type=module",
            "-e",
            "const m=await import(process.argv[1]); console.log(JSON.stringify(m.workspaceKey(process.argv[2])));",
            (run.installed / "shared/scope.mjs").as_uri(),
            str(run.workspace),
        ])
        assert key_process.returncode == 0
        workspace_key = json.loads(key_process.stdout)
        response = run.client.put("/v1/scope-bindings", json={"key": workspace_key, "scope_id": workspace_scope})
        response.raise_for_status()
        checks = []

        def prompt(expected: str, label: str, *, resume: str | None = None) -> str:
            marker = f"Synthetic scope probe {label}-{run.run_id}."
            start = len(run.wire.records)
            result = run.invoke(
                f"{marker} Call get_scope for {expected} using PowerContext MCP.",
                resume=resume,
                actions=[("get_scope", {"scope_id": expected})],
            )
            assert run.wire.result("get_scope", start)["scope_id"] == expected
            calls = [
                item
                for item in run.wire.records[start:]
                if item["path"] == "/v1/sources/content" and marker in item["request"].get("content", "")
            ]
            assert len(calls) == 1 and calls[0]["request"]["scope_id"] == expected, "scope_capture_contamination"
            checks.append({"case": label, "scope_id": expected, "native_mcp": True, "capture_scope_exact": True})
            return result["sessionId"]

        try:
            seed = prompt(run.scope_id, "explicit_over_workspace")
            session_key = {"integration": "zcode", "kind": "session", "external_id": seed}
            response = run.client.put("/v1/scope-bindings", json={"key": session_key, "scope_id": session_scope})
            response.raise_for_status()
            run.environment.pop("POWERCONTEXT_ZCODE_SCOPE_ID")
            prompt(session_scope, "session_over_workspace", resume=seed)
            prompt(workspace_scope, "workspace_for_new_session")
            run.post("/v1/scope-bindings/clear", {"key": workspace_key})
            resolved = run.post(
                "/v1/scope-bindings/resolve", {"explicit_scope_id": None, "binding_keys": [workspace_key]}
            )
            prompt(resolved["scope_id"], "server_default")

            nested = run.root / "second-workspace"
            nested.mkdir()
            initialized = run.process([run.git, "init", "--quiet", str(nested)])
            assert initialized.returncode == 0
            subprocess_result = run.process([
                str(run.node),
                "--input-type=module",
                "-e",
                "const m=await import(process.argv[1]); console.log(JSON.stringify(m.workspaceKey(process.argv[2])));",
                (run.installed / "shared/scope.mjs").as_uri(),
                str(nested),
            ])
            second_key = json.loads(subprocess_result.stdout)
            assert second_key != workspace_key
            run.client.put(
                "/v1/scope-bindings", json={"key": second_key, "scope_id": workspace_scope}
            ).raise_for_status()
            run.workspace = nested
            prompt(workspace_scope, "second_workspace_binding")
            run.workspace = original_workspace
            prompt(resolved["scope_id"], "first_workspace_still_default")
            assert (
                len({
                    record["identity"]["workspace"]
                    for path in (run.data_dir / "runtime").glob("*.json")
                    if (record := json.loads(path.read_text(encoding="utf-8")))
                })
                >= 2
            )
            assert all(
                record["identity"]["profile"] == sha256(str(run.data_dir.resolve()).encode()).hexdigest()
                for path in (run.data_dir / "runtime").glob("*.json")
                if (record := json.loads(path.read_text(encoding="utf-8")))
            )
            from .runner import serve

            original_paths = run.home, run.config_file, run.installed, run.data_dir
            original_url = run.base_url
            original_state = {path.name: path.read_bytes() for path in (run.data_dir / "runtime").glob("*.json")}
            alternate = run.root / "second-profile"
            alternate.mkdir()
            try:
                run.home = alternate
                run.config_file = alternate / ".zcode/cli/config.json"
                run.config_file.parent.mkdir(parents=True)
                shutil.copyfile(original_paths[1], run.config_file)
                isolated_config = json.loads(run.config_file.read_text(encoding="utf-8"))
                isolated_config["plugins"]["dirs"] = [
                    value
                    for value in isolated_config["plugins"]["dirs"]
                    if type(run.installed)(value).resolve() != original_paths[2].resolve()
                ]
                run.config_file.write_text(json.dumps(isolated_config), encoding="utf-8")
                personal = original_paths[0] / ".zcode/v2"
                if personal.is_dir():
                    target = alternate / ".zcode/v2"
                    target.mkdir()
                    for name in ("provider_config.json", "credentials.json"):
                        if (personal / name).is_file():
                            shutil.copyfile(personal / name, target / name)
                    if run.live:
                        run.environment["ZCODE_PERSONAL_PROVIDER_CONFIG_FILE"] = str(target / "provider_config.json")
                run.installed = alternate / ".zcode/cli/plugins/powercontext"
                run.environment.update(
                    HOME=str(alternate),
                    USERPROFILE=str(alternate),
                    ZCODE_STORAGE_DIR=str(alternate / ".zcode"),
                    ZCODE_DATA_BASE_DIR=str(alternate),
                    POWERCONTEXT_ZCODE_SCOPE_ID=run.scope_id,
                )
                run.install()
                listed = run.process([str(run.node), str(run.cli), "plugins", "list", "--json"])
                plugin = next(item for item in json.loads(listed.stdout) if item["name"] == "powercontext")
                run.data_dir = type(run.data_dir)(plugin["dataPath"])
                assert run.data_dir != original_paths[3]
                profile_session = prompt(run.scope_id, "second_profile")
                assert run.observations(profile_session)
                source_count = len(run.client.get(f"/v1/scopes/{run.scope_id}/sources").json()["items"])
                settings = ServerSettings(
                    _env_file=None,  # ty: ignore[unknown-argument] BaseSettings runtime option.
                    workspace=run.workspace,
                    database=SQLiteConfig(url=f"sqlite+aiosqlite:///{run.root / 'second-server.db'}"),
                    auth=BearerAuthConfig(
                        enabled=True, token=SecretStr(run.environment["POWERCONTEXT_ZCODE_AUTHORIZATION"][7:])
                    ),
                    runtime=RuntimeConfig(),
                    inference=InferenceConfig(),
                )
                with serve(ObserveServer(create_server_app(settings=settings), run.wire)) as second_url:
                    import httpx

                    with httpx.Client(
                        base_url=second_url,
                        headers={"Authorization": run.environment["POWERCONTEXT_ZCODE_AUTHORIZATION"]},
                    ) as client:
                        remote_scope = client.post(
                            "/v1/scopes",
                            json={
                                "title": "Synthetic second endpoint",
                                "summary": "Isolated endpoint ownership",
                                "idempotency_key": run.run_id,
                            },
                        ).json()["scope_id"]
                    run.base_url = second_url
                    run.environment["POWERCONTEXT_ZCODE_SCOPE_ID"] = remote_scope
                    run.install()
                    endpoint_session = prompt(remote_scope, "second_endpoint")
                    endpoint_records = run.observations(endpoint_session)
                    resolved_records = [
                        record for record in endpoint_records if record["stages"]["scope"]["state"] == "resolved"
                    ]
                    assert resolved_records and all(record["scope_id"] == remote_scope for record in resolved_records)
                    first_records = run.observations(profile_session)
                    assert first_records[0]["identity"]["endpoint"] != endpoint_records[0]["identity"]["endpoint"]
                    assert len(run.client.get(f"/v1/scopes/{run.scope_id}/sources").json()["items"]) == source_count
                assert original_state == {
                    path.name: path.read_bytes() for path in (original_paths[3] / "runtime").glob("*.json")
                }
            finally:
                run.home, run.config_file, run.installed, run.data_dir = original_paths
                run.base_url = original_url
            evidence.append(
                run.evidence(
                    "scope-priority",
                    {
                        "checks": checks,
                        "workspace_keys_distinct": True,
                        "profile_state_owned": True,
                        "second_profile_independent": True,
                        "second_endpoint_scope_owned": True,
                        "original_profile_state_unchanged": True,
                    },
                )
            )
        finally:
            run.environment = original_environment
            run.workspace = original_workspace
