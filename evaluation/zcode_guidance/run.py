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

"""Run the pinned Skill and a no-Skill control through the actual ZCode CLI app-server."""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from tests.e2e.zcode_acceptance.host import NativeHost
from tests.e2e.zcode_acceptance.runner import error_code, serve

from .fixture import PREFIX, SCOPE, GuidanceFixture, catalog
from .pin import LOCK, PROJECT, REPOSITORY, SOURCE, check
from .report import replay, write_json


class EvaluationHost(NativeHost):
    """Allow all fixture MCP operations so forbidden attempts remain observable."""

    def __init__(self, run: Any) -> None:
        self.permissions: list[dict] = []
        self.session = ""
        super().__init__(run)

    def dispatch(self, message: dict[str, Any]) -> None:
        if message.get("method") != "interaction/requestPermission":
            super().dispatch(message)
            return
        params = message.get("params", {})
        name = params.get("toolName", "")
        arguments = params.get("input", {})
        allowed = name in self.run.fixture_names
        command = arguments.get("command", "") if isinstance(arguments, dict) else ""

        # Only the installed read-only resolver, this workspace and this native session may run via shell permission.
        def quoted(value: str) -> str:
            return "(?:" + re.escape(value) + "|'" + re.escape(value) + "'|\"" + re.escape(value) + '")'

        expected = (
            r"(?:&\s*)?node\s+"
            + quoted(str(self.run.installed / "scripts/scope.mjs"))
            + r"\s+resolve\s+--cwd\s+"
            + quoted(str(self.run.workspace))
            + r"\s+--session-id\s+"
            + quoted(self.session)
            + r"\s*"
        )
        if self.session and isinstance(command, str) and re.fullmatch(expected, command, flags=re.IGNORECASE):
            allowed = True
        decision = "allow" if allowed else "deny"
        self.permissions.append({"tool": name, "input": arguments, "decision": decision})
        self.send(
            {
                "id": message["id"],
                "result": {
                    "decision": decision,
                    "reason": "Controlled fixture or exact read-only Scope resolver only.",
                },
            }
        )


class EvaluationCase:
    def __init__(
        self, root: Path, cli: Path, model_config: Path, tools: list[dict], host_model: str | None = None
    ) -> None:
        self.root, self.cli = root, cli
        self.node = shutil.which("node")
        if not self.node:
            raise ValueError("node_required")
        self.workspace = root / "workspace"
        self.home = root / "profile"
        self.installed = self.home / ".zcode/cli/plugins/powercontext"
        self.workspace.mkdir(parents=True)
        self.home.mkdir()
        self.scope_id = SCOPE
        self.run_id = uuid4().hex
        self.live = True
        self.budget = time.monotonic() + 600
        self.fixture_names = {PREFIX + tool["name"] for tool in tools}
        self.environment = {
            key: value for key, value in os.environ.items() if not key.startswith(("POWERCONTEXT_", "ZCODE_"))
        }
        self.environment.update(
            HOME=str(self.home),
            USERPROFILE=str(self.home),
            ZCODE_STORAGE_DIR=str(self.home / ".zcode"),
            ZCODE_DATA_BASE_DIR=str(self.home),
            ZCODE_CLI_BIN=str(cli),
            POWERCONTEXT_CLIENT_CONFIG_FILE=str(root / "client-settings.json"),
            POWERCONTEXT_ZCODE_SCOPE_ID=SCOPE,
            POWERCONTEXT_ZCODE_CAPTURE_PROMPTS="false",
            POWERCONTEXT_ZCODE_BOUNDARY_FLUSH="false",
            PYTHONPATH=str(REPOSITORY / "src"),
        )
        configured = json.loads(model_config.read_text(encoding="utf-8"))
        if configured.get("schemaVersion") != 1 or not isinstance(configured.get("config"), dict):
            raise ValueError("native_versioned_provider_config_required")
        self.host_selection = configured["config"].get("defaultModelSelection", {})
        if host_model:
            provider, model = host_model.split("/", 1)
            self.host_selection = {**self.host_selection, "providerId": provider, "modelId": model}
            configured["config"]["defaultModelSelection"] = self.host_selection
        if (
            not self.host_selection.get("providerId")
            or not self.host_selection.get("modelId")
            or not self.host_selection.get("options", {}).get("reasoningLevel")
        ):
            raise ValueError("default_model_selection_required")
        personal = self.home / ".zcode/v2/provider_config.json"
        write_json(personal, configured)
        self.environment["ZCODE_PERSONAL_PROVIDER_CONFIG_FILE"] = str(personal)
        if self.host_selection["providerId"].startswith("account:"):
            credentials = model_config.parent / "credentials.json"
            if not credentials.is_file():
                raise ValueError("account_credentials_required")
            shutil.copyfile(credentials, personal.parent / "credentials.json")
            secret = subprocess.check_output(
                [
                    self.node,
                    "-e",
                    'const os=require("node:os"); process.stdout.write(process.env.ZCODE_CREDENTIAL_SECRET || '
                    "`zcode-credential-fallback:${os.platform()}:${os.homedir()}:${os.userInfo().username}`)",
                ],
                text=True,
                encoding="utf-8",
                timeout=10,
            )  # noqa: S603
            self.environment["ZCODE_CREDENTIAL_SECRET"] = secret
        write_json(
            self.home / ".zcode/cli/config.json",
            {
                "locale": "en",
                "plugins": {"enabled": True, "dirs": []},
                "permission": {"allowedTools": sorted(self.fixture_names)},
            },
        )

    def process(self, arguments: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run(
            arguments,
            cwd=self.workspace,
            env=self.environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=True,
        )  # noqa: S603

    def install(self, url: str, *, with_skill: bool) -> dict:
        self.process(
            [
                sys.executable,
                "-c",
                "from powercontext.cli.zcode import install_zcode_plugin; import sys; "
                'install_zcode_plugin(source=sys.argv[1], ref="master", server_url=sys.argv[2])',
                str(REPOSITORY),
                url,
            ]
        )
        skill = self.installed / "skills/powercontext-project-context"
        if with_skill:
            expected = check()["files"]
            actual = {
                path.relative_to(skill).as_posix(): hashlib.sha256(
                    path.read_bytes().replace(b"\r\n", b"\n")
                ).hexdigest()
                for path in skill.rglob("*")
                if path.is_file()
            }
            if actual != expected:
                raise ValueError("installed_skill_pin_mismatch")
        else:
            shutil.rmtree(skill)
            manifest_path = self.installed / ".zcode-plugin/plugin.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest.pop("skills", None)
            write_json(manifest_path, manifest)
        listed = json.loads(self.process([self.node, str(self.cli), "plugins", "list", "--json"]).stdout)
        plugin = next(item for item in listed if item["name"] == "powercontext")
        if (
            not plugin["enabled"]
            or not plugin["mcpServerNames"]
            or plugin["diagnostics"]
            or plugin["skillCount"] != (1 if with_skill else 0)
        ):
            raise ValueError("native_plugin_discovery_failed")
        return {key: plugin[key] for key in ("name", "enabled", "mcpServerNames", "skillCount", "diagnostics")}


def execute_case(case: dict, run: EvaluationCase, fixture: GuidanceFixture, *, with_skill: bool) -> None:
    turns = []
    host = None
    try:
        with ExitStack() as stack:
            url = stack.enter_context(serve(fixture.app))
            write_json(run.root / "installation.json", run.install(url, with_skill=with_skill))
            host = EvaluationHost(run)
            stack.callback(host.close)
            mcp = host.request(
                "mcp/list",
                {"workspace": {"workspacePath": str(run.workspace), "workspaceKey": "acceptance-" + run.run_id}},
            )
            write_json(run.root / "mcp-discovery.json", mcp)
            status = mcp.get("statuses", {}).get("plugin:powercontext:powercontext", {})
            if status.get("status") != "connected" or status.get("toolCount") != len(fixture.tools):
                raise ValueError("complete_native_mcp_catalog_required")
            session = host.create(write=True)
            host.session = session
            write_json(run.root / "session-create.json", host.last_response)
            for index, prompt in enumerate(case["turns"]):
                fixture.turn = index + 1
                baseline = host.request("session/events", {"sessionId": session})["events"]
                sequence = max((event["seq"] for event in baseline), default=0)
                permission_start = len(host.permissions)
                turn = {
                    "prompt": prompt,
                    "response": "",
                    "native_events": [],
                    "mcp_calls": [],
                    "permissions": [],
                    "error": None,
                }
                turns.append(turn)
                try:
                    turn["response"] = host.prompt(session, prompt)["response"]
                except Exception as error:
                    turn["error"] = error_code(error)
                finally:
                    events = host.request("session/events", {"sessionId": session})["events"]
                    turn["native_events"] = [event for event in events if event["seq"] > sequence]
                    turn["mcp_calls"] = [call for call in fixture.calls if call["turn"] == index + 1]
                    turn["permissions"] = host.permissions[permission_start:]
                    write_json(run.root / "turns.json", turns)
                if turn["error"]:
                    break
    except Exception as error:
        write_json(run.root / "failure.json", {"error": error_code(error)})
        if host is not None and hasattr(host, "last_response"):
            write_json(run.root / "native-error.json", host.last_response)
    finally:
        write_json(run.root / "turns.json", turns)
        write_json(run.root / "mcp-transport.json", fixture.requests)
        # Remove opaque credential/model copies after the host exits. Retain only synthetic inputs and evidence.
        for name in ("provider_config.json", "credentials.json"):
            (run.home / ".zcode/v2" / name).unlink(missing_ok=True)


def execute(output: Path, cli: Path, model_config: Path, host_model: str | None = None) -> dict:
    lock = check()
    if output.exists():
        raise ValueError("new_output_directory_required")
    output.mkdir(parents=True)
    cli = cli.resolve(strict=True)
    tools = catalog()
    cases = json.loads((PROJECT / "cases.json").read_text(encoding="utf-8"))
    inputs = output / "inputs"
    inputs.mkdir()
    shutil.copyfile(PROJECT / "cases.json", inputs / "cases.json")
    shutil.copyfile(LOCK, inputs / "skill-lock.json")
    write_json(inputs / "catalog.json", tools)
    for name in lock["files"]:
        target = inputs / "skill" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((REPOSITORY / SOURCE / name).read_bytes().replace(b"\r\n", b"\n"))
    for path in PROJECT.glob("*.py"):
        target = inputs / "runner" / path.name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(path.read_bytes().replace(b"\r\n", b"\n"))

    def command(*args: str) -> str:
        return subprocess.check_output(list(args), text=True, encoding="utf-8", timeout=30).strip()  # noqa: S603

    provenance = {
        "started_at": datetime.now(UTC).isoformat(),
        "host_version": command("node", str(cli), "--version"),
        "host_bundle_sha256": hashlib.sha256(cli.read_bytes()).hexdigest(),
        "host_commit": command("git", "-C", str(cli.parent), "rev-parse", "HEAD"),
        "repository_commit": command("git", "-C", str(REPOSITORY), "rev-parse", "HEAD"),
        "repository_dirty": bool(command("git", "-C", str(REPOSITORY), "status", "--porcelain")),
        "model_mode": "live",
        "server_mode": "controlled-mcp",
        "hooks": "binding-and-empty-prepare-only",
        "provider_config_format": "native-versioned",
        "selected_model": None,
        "python_version": sys.version,
        "node_version": command("node", "--version"),
        "uv_lock_sha256": hashlib.sha256((REPOSITORY / "uv.lock").read_bytes()).hexdigest(),
    }
    write_json(output / "provenance.json", provenance)
    for arm in ("with_skill", "without_skill"):
        for case in cases:
            print(f"Running {arm}/{case['id']}", flush=True)
            root = output / arm / case["id"]
            try:
                run = EvaluationCase(root, cli, model_config.resolve(strict=True), tools, host_model)
                provenance["selected_model"] = run.host_selection
                execute_case(case, run, GuidanceFixture(case["id"], tools), with_skill=arm == "with_skill")
            except Exception as error:
                write_json(root / "failure.json", {"error": type(error).__name__})
                write_json(root / "turns.json", [])
                for name in ("provider_config.json", "credentials.json"):
                    (root / "profile/.zcode/v2" / name).unlink(missing_ok=True)
    provenance["finished_at"] = datetime.now(UTC).isoformat()
    write_json(output / "provenance.json", provenance)
    evidence = [path for path in inputs.rglob("*") if path.is_file()] + [output / "provenance.json"]
    for arm in ("with_skill", "without_skill"):
        for case in cases:
            root = output / arm / case["id"]
            evidence.extend(path for path in root.glob("*.json") if path.is_file())
    write_json(
        output / "manifest.json",
        {
            path.relative_to(output).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(evidence)
        },
    )
    report = replay(output)
    write_json(output / "report.json", report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cli", type=Path, default=os.environ.get("ZCODE_CLI_BIN"))
    parser.add_argument("--model-config", type=Path, required=True)
    parser.add_argument(
        "--host-model", help="Optional providerId/modelId override already configured in the native host."
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.cli is None:
        parser.error("--cli or ZCODE_CLI_BIN is required")
    report = execute(args.output.resolve(), args.cli, args.model_config, args.host_model)
    print(json.dumps({"qualified": report["qualified"], "results": report["results"]}, indent=2))
    raise SystemExit(0 if report["qualified"] else 1)


if __name__ == "__main__":
    main()
