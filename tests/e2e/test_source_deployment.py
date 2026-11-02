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

"""Load collect plugins through the public CLI against real Server and Runtime processes."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
from pathlib import Path

import httpx
import pytest
import yaml

from tests.source_helpers import protected, wait_until

EXAMPLES = Path(__file__).resolve().parents[2] / "examples/source_plugins"
DEPLOYMENT_TIMEOUT_SECONDS = 100


@pytest.mark.parametrize("remote", [False, True])
def test_load_real_background_processes_and_preserves_disabled_state(tmp_path, remote):  # noqa: C901 - real deployment lifecycle
    import hashlib
    import socket

    def port():
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            return sock.getsockname()[1]

    async def scenario():  # noqa: C901 - real deployment lifecycle
        tokens = {name: "load-e2e-" + name for name in ("deployer", "manager", "worker")}
        for name, token in tokens.items():
            protected(tmp_path / (name + ".token"), token)
        protected(
            tmp_path / "identities.json",
            json.dumps({
                "identities": [
                    {
                        "credential_id": name,
                        "token_digest": hashlib.sha256(token.encode()).hexdigest(),
                        "subject": {"type": "service", "id": name},
                    }
                    for name, token in tokens.items()
                ]
            }),
        )
        server_port, manager_port = port(), port()
        url = f"http://127.0.0.1:{server_port}"
        plugins = tmp_path / "plugins"
        plugins.mkdir()
        settings = {
            "POWERCONTEXT_SERVER_HTTP": {"host": "127.0.0.1", "port": server_port},
            "POWERCONTEXT_SERVER_DATABASE": {"kind": "sqlite", "url": f"sqlite+aiosqlite:///{tmp_path / 'server.db'}"},
            "POWERCONTEXT_SERVER_AUTH": {
                "enabled": True,
                "identities_file": str(tmp_path / "identities.json"),
                "administrators": ["service:deployer"],
            },
            "POWERCONTEXT_SERVER_ACCESS": {"mode": "enforced", "background_principal_id": "deployer"},
            "POWERCONTEXT_SERVER_RUNTIME": {"artifact_processing_families": []},
            "POWERCONTEXT_SERVER_SOURCE_MANAGEMENT": {"manager_subject": "service:manager"}
            if remote
            else {"state_dir": str(tmp_path / "deployment")},
        }
        values = {key: json.dumps(value) for key, value in settings.items()}
        values["POWERCONTEXT_SERVER_PUBLIC_URL"] = url
        if not remote:
            values["POWERCONTEXT_SERVER_SOURCE_PLUGINS_DIR"] = str(plugins)
        envfile = tmp_path / "server.env"
        protected(envfile, "\n".join(f"{key}='{value}'" for key, value in values.items()) + "\n")
        environment = {key: value for key, value in os.environ.items() if not key.startswith("POWERCONTEXT_SERVER_")}
        environment["POWERCONTEXT_HOME"] = str(tmp_path / "home")
        entrypoint = str(Path(sys.executable).with_name("powercontext"))

        async def cli(*args, success=True):
            child = await asyncio.create_subprocess_exec(
                entrypoint, *args, env=environment, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            try:
                out, err = await asyncio.wait_for(child.communicate(), DEPLOYMENT_TIMEOUT_SECONDS)
            finally:
                if child.returncode is None:
                    child.kill()
                    await child.wait()
            if success:
                assert child.returncode == 0, err.decode()
                return json.loads(out)
            assert child.returncode != 0
            return err.decode()

        manager_path = tmp_path / "manager.json" if remote else tmp_path / "deployment/manager.json"
        if remote:
            protected(
                manager_path,
                json.dumps({
                    "server": {"url": url, "credential_file": str(tmp_path / "manager.token")},
                    "deployment_id": "powercontext",
                    "manager_id": "sources",
                    "state_dir": str(tmp_path / "manager"),
                    "port": manager_port,
                }),
            )
        runtime_paths = []
        process = None
        with (tmp_path / "server.log").open("a") as log:

            async def start_server():
                child = await asyncio.create_subprocess_exec(
                    entrypoint, "server", "run", "--env-file", str(envfile), env=environment, stdout=log, stderr=log
                )
                async with httpx.AsyncClient(base_url=url) as client, asyncio.timeout(40):
                    while True:
                        assert child.returncode is None, (tmp_path / "server.log").read_text()
                        try:
                            if (await client.get("/health/ready")).status_code == 200:
                                return child
                        except httpx.HTTPError:
                            pass
                        await asyncio.sleep(0.1)

            try:
                process = await start_server()
                if remote:
                    await cli("source", "manager", "start", "--config", str(manager_path))
                async with httpx.AsyncClient(
                    base_url=url, headers={"Authorization": "Bearer " + tokens["deployer"]}, timeout=65
                ) as admin:
                    created = await admin.post(
                        "/v1/scopes", json={"title": "Load", "summary": "Real processes", "idempotency_key": "load"}
                    )
                    assert created.status_code == 201, created.text
                    scope = created.json()["scope_id"]
                    await admin.post(
                        "/v1/access/bindings/create",
                        json={
                            "subject": {"type": "service", "id": "deployer"},
                            "resource": {"type": "scope", "scope_id": scope},
                            "role": "scope.contributor",
                            "idempotency_key": "admin-read",
                        },
                    )
                    directory = plugins / "tickets"
                    shutil.copytree(EXAMPLES / "tickets", directory, ignore=shutil.ignore_patterns("__pycache__"))
                    (directory / "tickets.json").write_text('[{"id":"A","body":"alpha"},{"id":"B","body":"bravo"}]')
                    config = {
                        "id": "cli-tickets",
                        "plugin": ".",
                        "scope_ids": [scope],
                        "config": {"poll_interval": 0.1},
                        "targets": [
                            {
                                "scope_id": scope,
                                "capability": "tickets.item",
                                "capability_version": "1",
                                "input": {"id": "A"},
                            }
                        ],
                    }
                    if remote:
                        config["deployment"] = {
                            "server": {"url": url, "credential_file": str(tmp_path / "deployer.token")},
                            "management_url": f"http://127.0.0.1:{manager_port}",
                            "execution_credential_file": str(tmp_path / "worker.token"),
                            "state_dir": str(tmp_path / "runtime"),
                        }
                    path = directory / "powercontext.yml"
                    path.write_text(yaml.safe_dump(config))
                    args = ["source", "load", "--config", str(path)]
                    automatic = None
                    if not remote:
                        args += ["--env-file", str(envfile)]
                        # The Scope already exists; restart with a new manifest present before startup.
                        # Verify registration AND collection before invoking source load.
                        runtime_path = tmp_path / "deployment/instances/cli-tickets/runtime-config.json"
                        runtime_paths.append(runtime_path)
                        process.terminate()
                        await asyncio.wait_for(process.wait(), 25)
                        process = await start_server()
                        await wait_until(
                            lambda: admin.post(
                                "/v1/source-management/instances",
                                json={"instance_id": "cli-tickets", "view": "control"},
                            ),
                            lambda r: r.status_code == 200 and r.json()["instances"][0]["actual"] == "running",
                            # Server readiness precedes manager and Runtime startup; use the CLI deployment budget.
                            timeout_seconds=DEPLOYMENT_TIMEOUT_SECONDS,
                        )
                        await wait_until(
                            lambda: admin.get(f"/v1/scopes/{scope}/sources"),
                            lambda r: r.status_code == 200 and bool(r.json()["items"]),
                        )
                        automatic = await cli("source", "runtime", "status", "--config", str(runtime_path))
                    first = await cli(*args)
                    runtime_path = Path(
                        first.get("runtime_config", tmp_path / "deployment/instances/cli-tickets/runtime-config.json")
                    )
                    if runtime_path not in runtime_paths:
                        runtime_paths.append(runtime_path)
                    pid = first["service"]["pid"]
                    if not remote:
                        assert automatic is not None
                        assert pid == automatic["pid"]
                    repeated = await cli(*args)
                    assert repeated["service"]["pid"] == pid
                    added = await admin.post(
                        "/v1/source-management/add",
                        json={
                            "scope_id": scope,
                            "instance_id": "cli-tickets",
                            "capability": "tickets.item",
                            "capability_version": "1",
                            "input": {"id": "A"},
                        },
                    )
                    assert added.status_code == 200, added.text
                    target = added.json()["target_id"]
                    await wait_until(
                        lambda: admin.post(
                            "/v1/source-management/status", json={"scope_id": scope, "target_id": target}
                        ),
                        lambda r: r.status_code == 200 and bool(r.json()["source_refs"]),
                    )
                    runtime_config = json.loads(await asyncio.to_thread(runtime_path.read_text))
                    execution_token = (
                        await asyncio.to_thread(Path(runtime_config["server"]["credential_file"]).read_text)
                    ).strip()
                    denied = await admin.post(
                        "/v1/source-management/instance-set-enabled",
                        headers={"Authorization": "Bearer " + execution_token},
                        json={"instance_id": "cli-tickets", "enabled": False},
                    )
                    assert denied.status_code == 403
                    response = await admin.post(
                        "/v1/source-management/instance-set-enabled",
                        json={"instance_id": "cli-tickets", "enabled": False},
                    )
                    assert response.status_code == 200, response.text
                    await wait_until(
                        lambda: admin.post(
                            "/v1/source-management/instances", json={"instance_id": "cli-tickets", "view": "control"}
                        ),
                        lambda r: r.status_code == 200 and r.json()["instances"][0]["actual"] == "stopped",
                    )
                    config["targets"][0]["input"] = {"id": "B"}
                    path.write_text(yaml.safe_dump(config))
                    assert (await cli(*args))["service"]["pid"] == pid
                    process.terminate()
                    await asyncio.wait_for(process.wait(), 25)
                    process = await start_server()
                    state = await wait_until(
                        lambda: admin.post(
                            "/v1/source-management/instances", json={"instance_id": "cli-tickets", "view": "control"}
                        ),
                        lambda r: r.status_code == 200,
                    )
                    assert not state.json()["instances"][0]["enabled"]
                    assert (await cli(*args))["service"]["pid"] == pid
                    config["config"]["poll_interval"] = 0.2
                    path.write_text(yaml.safe_dump(config))
                    assert "conflict" in await cli(*args, success=False)
                    assert (await cli("source", "runtime", "status", "--config", str(runtime_path)))["pid"] == pid
                    # An explicit update cannot replace an outer Runtime that is still alive.
                    assert "instance-running" in await cli(*args, "--update", success=False)
                    await cli("source", "runtime", "stop", "--config", str(runtime_path))
                    await wait_until(
                        lambda: admin.post(
                            "/v1/source-management/instances", json={"instance_id": "cli-tickets", "view": "control"}
                        ),
                        lambda r: r.status_code == 200 and r.json()["instances"][0]["availability"] == "offline",
                    )
                    updated = await cli(*args, "--update")
                    assert updated["service"]["pid"] != pid
                    state = await admin.post(
                        "/v1/source-management/instances", json={"instance_id": "cli-tickets", "view": "control"}
                    )
                    assert state.status_code == 200, state.text
                    assert not state.json()["instances"][0]["enabled"]
                    assert state.json()["instances"][0]["actual"] == "stopped"
                    status = await admin.post(
                        "/v1/source-management/status", json={"scope_id": scope, "target_id": target}
                    )
                    assert status.status_code == 200 and status.json()["source_refs"]
                    enabled = await admin.post(
                        "/v1/source-management/instance-set-enabled",
                        json={"instance_id": "cli-tickets", "enabled": True},
                    )
                    assert enabled.status_code == 200, enabled.text
                    await wait_until(
                        lambda: admin.post(
                            "/v1/source-management/instances", json={"instance_id": "cli-tickets", "view": "control"}
                        ),
                        lambda r: r.status_code == 200 and r.json()["instances"][0]["actual"] == "running",
                    )
            finally:
                for runtime_path in runtime_paths:
                    if runtime_path.exists():
                        await cli("source", "runtime", "stop", "--config", str(runtime_path))
                if manager_path.exists():
                    await cli("source", "manager", "stop", "--config", str(manager_path))
                if process is not None and process.returncode is None:
                    process.terminate()
                    await asyncio.wait_for(process.wait(), 25)

    asyncio.run(scenario())
