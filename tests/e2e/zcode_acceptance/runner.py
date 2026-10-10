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

"""Managed installation, real Server and actual CLI acceptance composition."""

from __future__ import annotations

import json
import os
import platform
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import uvicorn
from pydantic import AnyHttpUrl, SecretStr

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime.config import InferenceConfig, RuntimeConfig
from powercontext.server.configuration import server_settings_context
from powercontext.server.factory import create_server_app
from powercontext.server.settings import BearerAuthConfig, ServerSettings

from .host import NativeHost
from .lifecycle import runtime_faults, server_recovery, session_lifecycle
from .protocol import HostModelFixture, ObserveServer, ToolAction, WireEvidence
from .scopes import scope_isolation
from .workflows import candidate_review, memory_and_handoff

REPOSITORY = Path(__file__).resolve().parents[3]
PLUGIN = REPOSITORY / "integrations/zcode/plugins/powercontext"


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def error_code(error: Exception) -> str:
    """Persist only a stable code, never an arbitrary exception message or request body."""
    code = str(error) if isinstance(error, AssertionError) else ""
    return code if re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_/]{0,95}", code) else type(error).__name__


@contextmanager
def serve(app: Any) -> Iterator[str]:
    """Own a pre-bound loopback socket and only stop this run's Server."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False))
    thread = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 15
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert server.started, "server_startup_failed"
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=15)
        listener.close()
        assert not thread.is_alive(), "server_shutdown_timeout"


class RestartableServer:
    """Own one loopback listener and real Server lifespans, allowing an actual outage test."""

    def __init__(self, factory: Any) -> None:
        self.factory = factory
        self.port = 0
        self.server: uvicorn.Server | None = None
        self.thread: threading.Thread | None = None

    def __enter__(self) -> RestartableServer:
        self.start()
        return self

    def start(self) -> None:
        listener = socket.socket()
        listener.bind(("127.0.0.1", self.port))
        self.port = listener.getsockname()[1]
        self.server = uvicorn.Server(
            uvicorn.Config(self.factory(), log_level="error", access_log=False, timeout_graceful_shutdown=5)
        )
        server = self.server
        self.thread = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
        self.thread.start()
        deadline = time.monotonic() + 15
        while not self.server.started and self.thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert self.server.started, "server_restart_failed"

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def stop(self) -> None:
        assert self.server and self.thread
        self.server.should_exit = True
        self.thread.join(timeout=15)
        assert not self.thread.is_alive(), "server_shutdown_timeout"

    def __exit__(self, *_: Any) -> None:
        if self.thread and self.thread.is_alive():
            self.stop()


class AcceptanceRun:
    def __init__(
        self,
        root: Path,
        *,
        live: bool,
        model_config: Path | None,
        generation_env: Path | None,
        host_model: str | None = None,
    ) -> None:
        self.run_id = uuid4().hex
        self.root = root.resolve() / self.run_id
        self.live = live
        self.model_config = model_config
        self.generation_env = generation_env
        self.requested_host_model = host_model
        self.node = shutil.which("node")
        self.git = shutil.which("git") or "git"
        self.cli = Path(os.environ.get("ZCODE_CLI_BIN", ""))
        self.wire = WireEvidence()
        self.model = None if live else HostModelFixture()
        self.environment = dict(os.environ)
        self.home = self.root / "profile"
        self.workspace = self.root / "workspace"
        self.config_file = self.home / ".zcode/cli/config.json"
        self.installed = self.home / ".zcode/cli/plugins/powercontext"
        self.budget = time.monotonic() + (900 if live else 300)
        self.scope_id = ""
        self.client: httpx.Client
        self.data_dir: Path
        self.observer: ObserveServer
        self.host: NativeHost | None = None
        self.host_selection: dict[str, Any] = {}
        self.summary: dict[str, Any] = {
            "schema": "powercontext.zcode.acceptance-run.v1",
            "run_id": self.run_id,
            "started_at": datetime.now(UTC).isoformat(),
            "finished_at": None,
            "repository_commit": None,
            "repository_dirty": None,
            "plugin_source_digest": None,
            "plugin_version": json.loads((PLUGIN / ".zcode-plugin/plugin.json").read_text())["version"],
            "host_kind": "open-source-cli",
            "host_version": None,
            "os": platform.system(),
            "model_mode": "live" if live else "fixture",
            "host_model": None,
            "generation_model": None,
            "server_mode": "real-sqlite",
            "transport_mode": "loopback-http",
            "scenarios": [
                {
                    "id": f"P3-{number:02}",
                    "status": "not_run",
                    "evidence_refs": [],
                    "failure_stage": None,
                    "stable_error_code": None,
                }
                for number in range(1, 11)
            ],
            "overall_status": "not_run",
            "limitations": ["Official Windows desktop requires separate manual execution."],
        }

    def evidence(self, name: str, value: Any) -> str:
        ref = f"evidence/{name}.json"
        write_json(self.root / ref, value)
        return ref

    @contextmanager
    def scenario(self, number: int) -> Iterator[list[str]]:
        record = self.summary["scenarios"][number - 1]
        try:
            yield record["evidence_refs"]
        except Exception as error:
            record.update(status="failed", failure_stage=record["id"], stable_error_code=error_code(error))
            raise
        else:
            record["status"] = "passed"

    def process(
        self, args: list[str], *, environment: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        remaining = self.budget - time.monotonic()
        assert remaining > 0, "acceptance_run_deadline_exceeded"
        return subprocess.run(
            args,
            env=environment or self.environment,
            cwd=self.workspace,
            text=True,
            encoding="utf-8",
            errors="strict",
            capture_output=True,
            check=False,
            timeout=min(180 if self.live else 45, remaining),
        )

    def invoke(
        self,
        prompt: str,
        *,
        actions: list[ToolAction] | None = None,
        recall: bool = False,
        resume: str | None = None,
        authorization: str | None = None,
        write: bool = False,
    ) -> dict[str, Any]:
        if self.model is not None:
            self.model.plan(actions, recall=recall)
        if write and self.live:
            assert self.host is not None
            session = self.host.create(write=True)
            return self.host.prompt(session, prompt, operations={name for name, _ in actions or []})
        environment = dict(self.environment)
        if authorization is not None:
            environment["POWERCONTEXT_ZCODE_AUTHORIZATION"] = authorization
        mode = ("build" if self.live else "yolo") if write else "plan"
        args = [
            str(self.node),
            str(self.cli),
            "--cwd",
            str(self.workspace),
            "--mode",
            mode,
            "--no-color",
            "--output-format",
            "stream-json",
            "--prompt",
            prompt,
        ]
        if resume:
            args.extend(["--resume", resume])
        completed = self.process(args, environment=environment)
        events = [json.loads(line) for line in completed.stdout.splitlines() if line.strip().startswith("{")]
        self.last_events = events
        if self.live:
            started = [
                payload
                for event in events
                if (payload := event.get("payload", {})).get("providerId") and payload.get("modelId")
            ]
            if started:
                actual = started[0]
                self.host_selection.update(providerId=actual["providerId"], modelId=actual["modelId"])
                self.summary["host_model"] = actual["providerId"] + "/" + actual["modelId"]
                assert not self.requested_host_model or self.summary["host_model"] == self.requested_host_model, (
                    "host_selected_different_model_from_requested"
                )
        if completed.returncode:
            failures = [payload for event in events if (payload := event.get("payload", {})).get("errorCode")]
            codes = [
                payload["errorCode"]
                for payload in failures
                if re.fullmatch(r"[a-zA-Z0-9_/-]{1,96}", payload["errorCode"])
            ]
            self.summary["host_error_codes"] = sorted(set(codes))
        assert completed.returncode == 0, f"host_process_exit_{completed.returncode}"
        results = [event for event in events if event.get("type") == "result"]
        assert len(results) == 1 and results[0].get("sessionId"), "host_result_missing"
        return results[0]

    def post(self, path: str, value: dict[str, Any]) -> dict[str, Any]:
        response = self.client.post(path, json=value)
        response.raise_for_status()
        return response.json()

    def observations(self, session: str) -> list[dict[str, Any]]:
        from hashlib import sha256

        session_hash = sha256(session.encode()).hexdigest()
        return [
            record
            for path in (self.data_dir / "runtime").glob("*.json")
            if (record := json.loads(path.read_text(encoding="utf-8"))).get("identity", {}).get("session")
            == session_hash
        ]

    def install(self) -> None:
        completed = self.process([
            sys.executable,
            "-c",
            "from powercontext.cli.zcode import install_zcode_plugin; "
            'import sys; install_zcode_plugin(source=sys.argv[1], ref="master", server_url=sys.argv[2])',
            str(REPOSITORY),
            self.base_url,
        ])
        assert completed.returncode == 0, "managed_install_failed"

    def setup(self, stack: ExitStack) -> None:
        assert self.node and self.cli.is_file(), "prerequisite_ZCODE_CLI_BIN_and_Node_required"
        self.home.mkdir(parents=True)
        self.workspace.mkdir()
        assert self.process([self.git, "init", "--quiet", str(self.workspace)]).returncode == 0, "workspace_init_failed"
        self.environment = {
            key: value for key, value in self.environment.items() if not key.startswith(("POWERCONTEXT_", "ZCODE_"))
        }
        self.environment.update(
            HOME=str(self.home),
            USERPROFILE=str(self.home),
            ZCODE_STORAGE_DIR=str(self.home / ".zcode"),
            ZCODE_DATA_BASE_DIR=str(self.home),
            ZCODE_CLI_BIN=str(self.cli.resolve()),
            POWERCONTEXT_CLIENT_CONFIG_FILE=str(self.root / "client-settings.json"),
            PYTHONPATH=str(REPOSITORY / "src"),
        )
        token = secrets.token_urlsafe(32)
        self.environment["POWERCONTEXT_ZCODE_AUTHORIZATION"] = "Bearer " + token
        inference = InferenceConfig()
        if self.live:
            assert self.model_config and self.model_config.is_file(), "prerequisite_host_model_config_required"
            assert self.generation_env and self.generation_env.is_file(), "prerequisite_generation_env_required"
            configured = json.loads(self.model_config.read_text(encoding="utf-8"))
            if configured.get("schemaVersion") == 1 and isinstance(configured.get("config"), dict):
                selection = configured["config"].get("defaultModelSelection")
                assert selection and selection.get("providerId") and selection.get("modelId"), (
                    "prerequisite_host_model_required"
                )
                assert configured["config"].get("providerConfigRules"), "prerequisite_host_provider_required"
                assert selection.get("options", {}).get("reasoningLevel"), "prerequisite_host_reasoning_level_required"
                if self.requested_host_model:
                    provider, model = self.requested_host_model.split("/", 1)
                    selection = {**selection, "providerId": provider, "modelId": model}
                    configured["config"]["defaultModelSelection"] = selection
                # Preserve the native versioned format; let the actual host validate and resolve its providers.
                personal = self.home / ".zcode/v2/provider_config.json"
                write_json(personal, configured)
                self.environment["ZCODE_PERSONAL_PROVIDER_CONFIG_FILE"] = str(personal)
                credentials = self.model_config.parent / "credentials.json"
                if selection["providerId"].startswith("account:"):
                    assert credentials.is_file(), "prerequisite_account_credentials_required"
                    # Opaque copy: the host owns decoding. No writes go back to the original credential store.
                    shutil.copyfile(credentials, personal.parent / "credentials.json")
                    original_secret = subprocess.check_output(
                        [
                            str(self.node),
                            "-e",
                            'const os=require("node:os"); process.stdout.write(process.env.ZCODE_CREDENTIAL_SECRET || '
                            "`zcode-credential-fallback:${os.platform()}:${os.homedir()}:${os.userInfo().username}`)",
                        ],
                        text=True,
                        encoding="utf-8",
                        timeout=10,
                    )
                    self.environment["ZCODE_CREDENTIAL_SECRET"] = original_secret
                host_settings = {}
                self.host_selection = selection
                self.summary["host_model"] = selection["providerId"] + "/" + selection["modelId"]
            else:
                assert configured.get("provider") and configured.get("model", {}).get("main"), (
                    "prerequisite_host_model_required"
                )
                host_settings = {key: configured[key] for key in ("provider", "model")}
                if self.requested_host_model:
                    host_settings["model"] = {"main": self.requested_host_model}
                self.summary["host_model"] = host_settings["model"]["main"]
                provider, model = host_settings["model"]["main"].split("/", 1)
                self.host_selection = {"providerId": provider, "modelId": model, "options": {"reasoningLevel": "max"}}
            settings_context = stack.enter_context(server_settings_context(env_file=self.generation_env))
            inference = settings_context.inference
            assert inference.generation_model, "prerequisite_generation_model_required"
            self.summary["generation_model"] = inference.generation_model
        else:
            assert self.model is not None
            model_url = stack.enter_context(serve(self.model.app))
            host_settings = {
                "provider": {
                    "acceptance": {
                        "kind": "openai-compatible",
                        "options": {"apiKey": "synthetic-only", "baseURL": model_url + "/v1"},
                        "models": {"fixture": {"id": "fixture", "contextWindow": 65536, "tool_call": True}},
                    }
                },
                "model": {"main": "acceptance/fixture"},
            }
            inference = InferenceConfig(
                generation_model="openai-chat:memory-fixture",
                generation_base_url=AnyHttpUrl(model_url + "/v1"),
                generation_headers={"Authorization": SecretStr("Bearer synthetic-generation-only")},
            )
            self.summary["generation_model"] = "openai-chat:memory-fixture"
        if not self.live:
            self.summary["host_model"] = host_settings["model"]["main"]
            self.host_selection = {
                "providerId": "acceptance",
                "modelId": "fixture",
                "options": {"reasoningLevel": "disabled"},
            }
        server_settings = ServerSettings(
            _env_file=None,  # ty: ignore[unknown-argument] BaseSettings runtime option.
            workspace=self.workspace,
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{self.root / 'server.db'}"),
            auth=BearerAuthConfig(enabled=True, token=SecretStr(token)),
            runtime=RuntimeConfig(memory_schedule_seconds=0.25, artifact_processing_families=("memory",)),
            inference=inference,
        )

        def create_observed_server():
            app = create_server_app(settings=server_settings, scheduler_path=self.root / "scheduler")
            self.observer = ObserveServer(app, self.wire)
            return self.observer

        self.server = stack.enter_context(RestartableServer(create_observed_server))
        self.base_url = self.server.url
        self.client = stack.enter_context(
            httpx.Client(
                base_url=self.base_url,
                headers={"Authorization": "Bearer " + token},
                timeout=15,
            )
        )
        self.summary["repository_commit"] = subprocess.check_output(
            [shutil.which("git") or "git", "rev-parse", "HEAD"],
            cwd=REPOSITORY,
            text=True,
            timeout=10,
        ).strip()
        self.summary["repository_dirty"] = bool(
            subprocess.check_output(
                [self.git, "status", "--porcelain"],
                cwd=REPOSITORY,
                text=True,
                timeout=10,
            ).strip()
        )
        digest = sha256()
        for path in sorted(path for path in PLUGIN.rglob("*") if path.is_file()):
            digest.update(path.relative_to(PLUGIN).as_posix().encode() + b"\0" + path.read_bytes())
        self.summary["plugin_source_digest"] = "sha256:" + digest.hexdigest()
        version = self.process([str(self.node), str(self.cli), "--version"])
        assert version.returncode == 0, "host_version_unavailable"
        self.summary["host_version"] = version.stdout.strip()
        approved_operations = [
            "remember_memory",
            "revise_memory_entry",
            "handoff_current_work",
            "commit_handoff",
            "acknowledge_handoff",
            "record_task_outcome",
            "approve_candidate",
        ]
        write_json(
            self.config_file,
            {
                "locale": "en",
                "plugins": {"enabled": True, "dirs": []},
                **host_settings,
                "permission": {
                    "allowedTools": ["mcp__plugin_powercontext_powercontext__" + name for name in approved_operations]
                },
            },
        )
        with self.scenario(1) as evidence:
            self.install()
            before = json.loads(self.config_file.read_text(encoding="utf-8"))
            self.install()
            assert json.loads(self.config_file.read_text(encoding="utf-8")) == before, "upgrade_changed_other_settings"
            listed = self.process([str(self.node), str(self.cli), "plugins", "list", "--json"])
            assert listed.returncode == 0, "host_plugin_discovery_failed"
            plugin = next(item for item in json.loads(listed.stdout) if item["name"] == "powercontext")
            assert plugin["enabled"] and len(plugin["mcpServerNames"]) == 1 and not plugin["diagnostics"]
            assert plugin["mcpServerNames"][0].split(":")[-1] == "powercontext"
            assert Path(plugin["rootPath"]).resolve() == self.installed.resolve()
            self.data_dir = Path(plugin["dataPath"])
            evidence.append(
                self.evidence(
                    "managed-install",
                    {
                        "enabled": plugin["enabled"],
                        "mcp": plugin["mcpServerNames"],
                        "diagnostics": plugin["diagnostics"],
                        "managed_copy": True,
                        "upgrade_preserved_config": True,
                    },
                )
            )
        scope = self.post(
            "/v1/scopes",
            {
                "title": "ZCode acceptance " + self.run_id,
                "summary": "Disposable synthetic host acceptance",
                "idempotency_key": self.run_id,
            },
        )
        self.scope_id = scope["scope_id"]
        self.environment["POWERCONTEXT_ZCODE_SCOPE_ID"] = self.scope_id
        if self.live:
            self.host = NativeHost(self)
            stack.callback(self.host.close)

    def connectivity(self) -> None:
        with self.scenario(2) as evidence:
            rejections = []
            for header in ("", "Bearer deliberately-wrong-synthetic-token"):
                start = len(self.wire.records)
                self.invoke(
                    "Call the PowerContext list_scopes MCP tool. Report tool unavailability faithfully.",
                    authorization=header,
                )
                rejects = [
                    item["status"]
                    for item in self.wire.records[start:]
                    if item["path"] == "/mcp" and item["status"] == 401
                ]
                assert rejects, "native_MCP_missing_auth_rejection"
                rejections.append(rejects)
            start = len(self.wire.records)
            result = self.invoke(
                f"Call PowerContext list_scopes and get_scope for {self.scope_id}. Use only the native MCP tools.",
                actions=[("list_scopes", {}), ("get_scope", {"scope_id": self.scope_id})],
            )
            scopes = self.wire.result("list_scopes", start)
            assert any(item["scope_id"] == self.scope_id for item in scopes["items"])
            assert self.wire.result("get_scope", start)["scope_id"] == self.scope_id
            evidence.append(
                self.evidence(
                    "native-mcp-auth",
                    {
                        "rejections": rejections,
                        "operations": ["list_scopes", "get_scope"],
                        "scope_id": self.scope_id,
                        "session_distinct": bool(result["sessionId"]),
                    },
                )
            )

    def automatic_memory(self) -> None:
        with self.scenario(3) as evidence:
            project = "Orion" + self.run_id[:8]
            marker = "copper-" + str(secrets.randbelow(900_000) + 100_000)
            prompt = (
                f"In the synthetic {project} project, the verification color is {marker}. "
                "This is a confirmed, ongoing deployment policy for this codebase: every future release must "
                "include this exact verification color in its deployment checklist, and a mismatch blocks release. "
                "Explain this project constraint briefly. Do not explicitly call any memory writing tool."
            )
            start = len(self.wire.records)
            first = self.invoke(prompt)
            sources = self.client.get(f"/v1/scopes/{self.scope_id}/sources").json()["items"]
            captured = [item for item in sources if marker in item["content"]]
            assert len(captured) == 1, "automatic_capture_missing_or_duplicated"
            source = captured[0]
            source_ref = {"name": "content", "source_id": source["source_id"]}
            deadline = min(self.budget, time.monotonic() + (180 if self.live else 20))
            generated = None
            inventory: dict[str, Any] = {}
            while time.monotonic() < deadline:
                inventory = self.post("/v1/memory/entries/list", {"scope_id": self.scope_id})
                generated = next(
                    (
                        item
                        for item in inventory["entries"]
                        if marker in item["text"] and source_ref in item["source_refs"]
                    ),
                    None,
                )
                if generated:
                    break
                time.sleep(0.1)
            assert generated and inventory["memory"], "scheduler_memory_timeout_or_missing_source_evidence"
            assert not any(item["path"] == "/v1/memory/flush" for item in self.wire.records[start:])
            assert not self.wire.calls("remember_memory", start), "auto_chain_substituted_manual_memory"
            second = self.invoke(
                f"What is the verification color in the synthetic {project} project? Answer with its color code only.",
                recall=True,
            )
            assert second["sessionId"] != first["sessionId"], "recall_reused_session"
            assert marker in second["response"], "fresh_session_failed_to_recall"
            prepared = [
                item
                for item in self.wire.records[start:]
                if item["path"] == "/v1/context/prepare" and marker in (item["response"].get("content") or "")
            ]
            assert prepared, "server_prepared_context_missing"
            observations = self.observations(second["sessionId"])
            recalled = [item for item in observations if item["event"] == "UserPromptSubmit"]
            assert recalled and recalled[-1]["stages"]["prepare"]["state"] == "ready"
            assert recalled[-1]["stages"]["context_output"]["state"] == "emitted"
            assert recalled[-1]["scope_id"] == self.scope_id
            evidence.append(
                self.evidence(
                    "automatic-memory",
                    {
                        "scope_id": self.scope_id,
                        "source": source_ref,
                        "position": source["position"],
                        "memory": inventory["memory"],
                        "citation": generated["citation"],
                        "source_refs": generated["source_refs"],
                        "independent_sessions": True,
                        "random_marker_recalled": True,
                        "prepare": "ready",
                        "context_output": "emitted",
                        "flush_requests": 0,
                        "remember_calls": 0,
                        "boundary_flush": False,
                    },
                )
            )

    def run(self) -> Path:
        self.root.mkdir(parents=True, exist_ok=False)
        try:
            with ExitStack() as stack:
                self.setup(stack)
                self.connectivity()
                self.automatic_memory()
                scope_isolation(self)
                memory_and_handoff(self)
                candidate_review(self)
                session_lifecycle(self)
                runtime_faults(self)
                server_recovery(self)
            assert all(item["status"] == "passed" for item in self.summary["scenarios"][:9]), (
                "core_scenarios_incomplete"
            )
            self.summary["overall_status"] = "passed"
            self.summary["limitations"].append("Optional P3-10 HTTPS endpoint was not selected.")
        except Exception as error:
            self.summary["overall_status"] = "failed"
            self.summary["stable_error_code"] = error_code(error)
            failed = next((item for item in self.summary["scenarios"] if item["status"] == "failed"), None)
            self.summary["failure_stage"] = failed["failure_stage"] if failed else "prerequisites"
            self.summary["limitations"].append("Acceptance failed: " + error_code(error))
            raise
        finally:
            self.summary["finished_at"] = datetime.now(UTC).isoformat()
            write_json(self.root / "summary.json", self.summary)
        return self.root / "summary.json"
