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

import argparse
import importlib
import importlib.util
import json
import sys
import threading
import types
from pathlib import Path
from typing import Any

import pytest

HERMES_ROOT = Path(__file__).parents[2] / "integrations" / "hermes"
_HERMES_MODULE_NAMES = (
    "plugins.powercontext",
    "plugins.powercontext.provider",
    "plugins.powercontext.commands",
    "plugins.powercontext.trace",
    "plugins.powercontext.workstream",
    "plugins.powercontext.operations",
    "plugins.powercontext.helpers",
    "plugins.powercontext.client",
    "plugins.powercontext.cli",
)


@pytest.fixture
def hermes_modules(monkeypatch):
    previous_modules = {name: sys.modules.get(name) for name in _HERMES_MODULE_NAMES}
    for name in _HERMES_MODULE_NAMES:
        sys.modules.pop(name, None)
    monkeypatch.syspath_prepend(str(HERMES_ROOT))
    try:
        yield importlib.import_module("plugins.powercontext"), importlib.import_module("plugins.powercontext.cli")
    finally:
        for name in _HERMES_MODULE_NAMES:
            sys.modules.pop(name, None)
        for name, module in previous_modules.items():
            if module is not None:
                sys.modules[name] = module


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.base_url = "http://powercontext.test:8000"
        self._remember_count = 0
        self._revision = 0
        self._memory_entries: dict[str, dict[str, Any]] = {}
        self.memory_extraction = True

    def prepare_context(self, scope_id, query, *, max_bytes):
        self.calls.append(("prepare_context", (scope_id, query), {"max_bytes": max_bytes}))
        return {"status": "ready", "content": "remembered project context"}

    def capture_content(self, scope_id, *, source_id, content, metadata):
        self.calls.append(("capture_content", (scope_id, source_id, content), {"metadata": metadata}))
        return {}

    def flush_memory(self, scope_id):
        self.calls.append(("flush_memory", (scope_id,), {}))
        return {}

    def search_memory(self, scope_id, query, *, limit, mode):
        self.calls.append(("search_memory", (scope_id, query), {"limit": limit, "mode": mode}))
        hits = [
            {"text": text, "citation": citation}
            for text, citation in self._memory_entries.items()
            if query.lower() in text.lower()
        ]
        return {"hits": hits or [{"text": "a memory"}]}

    def get_memory_entry(self, scope_id, citation):
        self.calls.append(("get_memory_entry", (scope_id, citation), {}))
        return {"text": "a memory"}

    def remember_memory(self, scope_id, *, kind, text, reason=None):
        self._remember_count += 1
        self._revision += 1
        for citation in self._memory_entries.values():
            citation["memory_ref"]["revision"] = self._revision
        citation = {
            "memory_ref": {
                "family": "memory",
                "artifact_id": f"memory-{self._remember_count}",
                "revision": self._revision,
            },
            "entry_id": f"entry-{self._remember_count}",
            "entry_version_id": f"entry-version-{self._remember_count}",
        }
        self._memory_entries[text] = citation
        self.calls.append(("remember_memory", (scope_id, kind, text), {"reason": reason}))
        return {
            "status": "remembered",
            "entry": {"citation": citation},
        }

    def retire_memory_entry(self, scope_id, citation, *, reason=None):
        assert citation["memory_ref"]["revision"] == self._revision
        self._revision += 1
        identity = (citation["entry_id"], citation["entry_version_id"])
        for text, stored in list(self._memory_entries.items()):
            if (stored["entry_id"], stored["entry_version_id"]) == identity:
                del self._memory_entries[text]
                break
        self.calls.append(("retire_memory_entry", (scope_id, citation), {"reason": reason}))
        return {"status": "retired"}

    def get_liveness(self):
        self.calls.append(("get_liveness", (), {}))
        return {"status": "ok"}

    def get_readiness(self):
        self.calls.append(("get_readiness", (), {}))
        return {"status": "ready"}

    def get_capabilities(self):
        self.calls.append(("get_capabilities", (), {}))
        return {"memory_extraction": self.memory_extraction}

    def request_operation(self, operation, payload):
        self.calls.append(("request_operation", (operation, payload), {}))
        return {"operation": operation, "payload": payload}


@pytest.fixture
def provider_and_client(tmp_path, hermes_modules):
    provider_module, _cli_module = hermes_modules
    client = FakeClient()
    provider = provider_module.PowerContextMemoryProvider(
        {"scope_id": "hermes:{profile}:{user_id}"},
        client_factory=lambda _config: client,
    )
    provider.initialize("session-1", hermes_home=str(tmp_path), agent_identity="coder", user_id="user-7")
    yield provider, client
    provider.shutdown()


def test_prefetch_uses_profile_and_user_scoped_context(provider_and_client):
    provider, client = provider_and_client

    recalled = provider.prefetch("What did we decide about the deployment?")

    assert "remembered project context" in recalled
    assert client.calls[0] == (
        "prepare_context",
        ("hermes:coder:user-7", "What did we decide about the deployment?"),
        {"max_bytes": 8000},
    )


def test_evaluation_trace_is_partitioned_by_session_and_records_parent(tmp_path, hermes_modules):
    provider_module, _cli_module = hermes_modules
    client = FakeClient()
    provider = provider_module.PowerContextMemoryProvider(
        {"evaluation_trace": True},
        client_factory=lambda _config: client,
    )
    provider.initialize("session-1", hermes_home=str(tmp_path), agent_identity="coder", user_id="user-7")

    provider.prefetch("first query")
    provider.on_session_switch("session-2", parent_session_id="session-1")
    provider.prefetch("second query")
    provider.shutdown()

    trace_dir = tmp_path / "powercontext" / "evaluation-trace"
    session_files = sorted((trace_dir / "sessions").glob("*.jsonl"))
    assert len(session_files) == 2

    session_events = [json.loads(line) for line in session_files[0].read_text(encoding="utf-8").splitlines()]
    child_events = [json.loads(line) for line in session_files[1].read_text(encoding="utf-8").splitlines()]
    all_events = session_events + child_events
    assert {event["session_id"] for event in all_events} == {"session-1", "session-2"}
    assert {event["profile"] for event in all_events} == {"coder"}
    assert any(
        event["event_type"] == "powercontext_injection" and event["query"] == "first query" for event in all_events
    )
    assert any(
        event["event_type"] == "session_switch"
        and event["session_id"] == "session-2"
        and event["parent_session_id"] == "session-1"
        for event in all_events
    )

    index_events = [json.loads(line) for line in (trace_dir / "index.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [event["session_id"] for event in index_events] == ["session-1", "session-2"]
    assert index_events[1]["parent_session_id"] == "session-1"


def test_evaluation_trace_slash_command_reads_named_session(tmp_path, hermes_modules):
    provider_module, _cli_module = hermes_modules
    provider = provider_module.PowerContextMemoryProvider(
        {"evaluation_trace": True},
        client_factory=lambda _config: FakeClient(),
    )
    provider.initialize("session-1", hermes_home=str(tmp_path), agent_identity="coder", user_id="user-7")
    provider.prefetch("trace me")

    status = json.loads(provider.handle_slash_command("trace status"))
    shown = json.loads(provider.handle_slash_command("trace show --session session-1"))
    sessions = json.loads(provider.handle_slash_command("trace sessions"))
    cleared = provider.handle_slash_command("trace clear --session session-1")
    remaining_sessions = json.loads(provider.handle_slash_command("trace sessions"))
    provider.shutdown()

    assert status["enabled"] is True
    assert status["session_id"] == "session-1"
    assert any(event.get("query") == "trace me" for event in shown)
    assert sessions[0]["session_id"] == "session-1"
    assert "Cleared evaluation trace" in cleared
    assert remaining_sessions == []


def test_register_does_not_install_session_bound_slash_handlers(hermes_modules):
    provider_module, _cli_module = hermes_modules

    class Context:
        def __init__(self):
            self.provider = None
            self.commands = {}
            self.skills = {}

        def register_memory_provider(self, provider):
            self.provider = provider

        def register_command(self, name, handler, **kwargs):
            self.commands[name] = (handler, kwargs)

        def register_skill(self, name, path, description=None):
            self.skills[name] = (path, description)

    context = Context()
    provider_module.register(context)

    assert context.provider is not None
    assert context.commands == {}
    assert "powercontext" in context.skills


def test_powercontext_subcommands_are_available_to_hermes_completer(hermes_modules, monkeypatch):
    _provider_module, _cli_module = hermes_modules
    commands_module = importlib.import_module("plugins.powercontext.commands")
    host_commands = types.ModuleType("hermes_cli.commands")
    host_commands.__dict__["SUBCOMMANDS"] = {}
    monkeypatch.setitem(sys.modules, "hermes_cli", types.ModuleType("hermes_cli"))
    monkeypatch.setitem(sys.modules, "hermes_cli.commands", host_commands)

    commands_module.register_subcommands()

    expected = list(commands_module.POWERCONTEXT_SUBCOMMANDS)
    subcommands = host_commands.__dict__["SUBCOMMANDS"]
    assert subcommands["/pc"] == expected
    assert subcommands["/powercontext"] == expected


def test_standalone_command_companion_registers_before_agent_and_forwards():
    module_name = "plugins.powercontext_command_test"
    module_path = HERMES_ROOT / "plugins" / "powercontext-command" / "__init__.py"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)

        class Provider:
            name = "powercontext"

            def handle_slash_command(self, raw_args):
                return f"handled: {raw_args}"

        class Context:
            def __init__(self):
                self.commands = {}
                self._manager: Any = type("Manager", (), {"_cli_ref": None})()

            def register_command(self, name, handler, **kwargs):
                self.commands[name] = (handler, kwargs)

        context = Context()
        module.register(context)

        assert set(context.commands) >= {"pc", "powercontext"}
        handler = context.commands["pc"][0]
        assert "not initialized" in handler("status").lower()
        assert context.commands["powercontext"][0]("status") == handler("status")

        context._manager._cli_ref = type(
            "Cli",
            (),
            {
                "agent": type(
                    "Agent",
                    (),
                    {"_memory_manager": type("MemoryManager", (), {"providers": [Provider()]})()},
                )()
            },
        )()
        assert handler("status") == "handled: status"
    finally:
        sys.modules.pop(module_name, None)


def test_standalone_command_companion_keeps_interleaved_sessions_isolated():
    module_name = "plugins.powercontext_command_isolation_test"
    module_path = HERMES_ROOT / "plugins" / "powercontext-command" / "__init__.py"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)

        class Provider:
            name = "powercontext"

            def __init__(self, scope_id):
                self.scope_id = scope_id
                self.calls = []

            def handle_slash_command(self, raw_args):
                self.calls.append(raw_args)
                return self.scope_id

        class Context:
            def __init__(self):
                self.commands = {}
                self._manager: Any = type("Manager", (), {"_cli_ref": None})()

            def register_command(self, name, handler, **kwargs):
                self.commands[name] = (handler, kwargs)

        context = Context()
        module.register(context)
        handler = context.commands["pc"][0]
        alice = Provider("review:alice")
        bob = Provider("review:bob")

        def activate(provider):
            context._manager._cli_ref = type(
                "Cli",
                (),
                {
                    "agent": type(
                        "Agent",
                        (),
                        {"_memory_manager": type("MemoryManager", (), {"providers": [provider]})()},
                    )()
                },
            )()

        for provider in (alice, bob, alice, bob):
            activate(provider)
            assert handler("status") == provider.scope_id

        assert alice.calls == ["status", "status"]
        assert bob.calls == ["status", "status"]

        # Gateway dispatch has no caller context in Hermes v0.20.4. It must
        # not reuse whichever interactive Agent happened to be active last.
        context._manager._cli_ref = None
        assert "not initialized" in handler("status").lower()
        assert alice.calls == ["status", "status"]
        assert bob.calls == ["status", "status"]
    finally:
        sys.modules.pop(module_name, None)


def test_queue_prefetch_honors_max_bytes_environment_override(provider_and_client, monkeypatch):
    provider, client = provider_and_client
    monkeypatch.setenv("POWERCONTEXT_HERMES_MAX_BYTES", "16000")

    provider.queue_prefetch("What did we decide about the deployment?")
    provider._wait_for_background()

    assert client.calls[0] == (
        "prepare_context",
        ("hermes:coder:user-7", "What did we decide about the deployment?"),
        {"max_bytes": 16000},
    )


def test_queue_prefetch_does_not_wait_for_http(provider_and_client):
    provider, client = provider_and_client
    started = threading.Event()
    release = threading.Event()
    caller_done = threading.Event()

    def blocked_prepare(*args, **kwargs):
        started.set()
        release.wait(timeout=1)
        return {"status": "ready", "content": "context"}

    client.prepare_context = blocked_prepare
    caller = threading.Thread(
        target=lambda: (provider.queue_prefetch("query"), caller_done.set()),
        daemon=True,
    )
    caller.start()

    assert started.wait(timeout=1)
    assert caller_done.wait(timeout=0.2)
    release.set()
    caller.join(timeout=1)
    provider._wait_for_background()


def test_json_config_is_loaded_from_hermes_home(tmp_path, hermes_modules):
    provider_module, _cli_module = hermes_modules
    client = FakeClient()
    provider = provider_module.PowerContextMemoryProvider(client_factory=lambda config: client)
    config_path = tmp_path / "powercontext" / "config.json"
    config_path.parent.mkdir()
    config_path.write_text(
        json.dumps({
            "base_url": "http://powercontext.test:9000",
            "scope_id": "hermes:{profile}:{user_id}",
            "max_bytes": 1200,
            "capture_turns": False,
        }),
        encoding="utf-8",
    )

    provider.initialize("session-1", hermes_home=str(tmp_path), agent_identity="coder", user_id="user-7")

    assert provider._client is client
    assert provider._config["base_url"] == "http://powercontext.test:9000"
    assert provider._config["max_bytes"] == 1200
    assert provider._config["capture_turns"] is False
    assert provider._scope_id == "hermes:coder:user-7"
    provider.shutdown()


def test_memory_setup_schema_exposes_powercontext_configuration(hermes_modules):
    provider_module, _cli_module = hermes_modules
    provider = provider_module.PowerContextMemoryProvider()

    schema = provider.get_config_schema()
    fields = {field["key"]: field for field in schema}

    assert fields["base_url"]["default"] == "http://127.0.0.1:8000"
    assert fields["authorization"]["secret"] is True
    assert fields["authorization"]["env_var"] == "POWERCONTEXT_HERMES_AUTHORIZATION"
    assert fields["capture_pre_compress"]["choices"] == ["true", "false"]
    assert "capture_turns" in fields
    assert "flush_on_session_end" in fields
    assert fields["workstream_persistence"]["default"] == "true"


def test_memory_setup_saves_powercontext_json_and_preserves_existing_values(tmp_path, hermes_modules):
    provider_module, _cli_module = hermes_modules
    provider = provider_module.PowerContextMemoryProvider()
    config_path = tmp_path / "powercontext" / "config.json"
    config_path.parent.mkdir()
    config_path.write_text(
        json.dumps({"base_url": "http://powercontext.test:8000", "custom_setting": "keep-me"}),
        encoding="utf-8",
    )

    provider.save_config(
        {"base_url": "http://powercontext.test:9000", "max_bytes": "16000"},
        str(tmp_path),
    )

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved == {
        "base_url": "http://powercontext.test:9000",
        "custom_setting": "keep-me",
        "max_bytes": "16000",
    }


def test_sync_turn_is_flushed_before_session_end(provider_and_client):
    provider, client = provider_and_client

    provider.sync_turn("Use uv for the integration.", "I will add a uv check.", session_id="session-1")
    provider.on_session_end([])

    names = [call[0] for call in client.calls]
    assert names == ["capture_content", "get_capabilities", "flush_memory"]
    assert client.calls[0][1][0] == "hermes:coder:user-7"
    assert client.calls[0][2]["metadata"]["kind"] == "hermes-turn"


def test_sync_turn_does_not_wait_for_http(provider_and_client):
    provider, client = provider_and_client
    started = threading.Event()
    release = threading.Event()
    caller_done = threading.Event()

    def blocked_capture(*args, **kwargs):
        started.set()
        release.wait(timeout=1)
        return {}

    client.capture_content = blocked_capture
    caller = threading.Thread(
        target=lambda: (provider.sync_turn("user", "assistant"), caller_done.set()),
        daemon=True,
    )
    caller.start()

    assert started.wait(timeout=1)
    assert caller_done.wait(timeout=0.2)
    release.set()
    caller.join(timeout=1)
    provider._wait_for_background()


def test_session_end_skips_flush_when_memory_extraction_is_disabled(provider_and_client):
    provider, client = provider_and_client
    client.memory_extraction = False

    provider.sync_turn("Captured as a Source.", "No extraction is available.", session_id="session-1")
    provider.on_session_end([])

    assert [call[0] for call in client.calls] == ["capture_content", "get_capabilities"]


def test_pre_compress_persists_context_before_compression(provider_and_client):
    provider, client = provider_and_client
    provider._config["capture_pre_compress"] = True

    result = provider.on_pre_compress([
        {"role": "user", "content": "The service must stay backward compatible."},
        {"role": "assistant", "content": "I will preserve the public API."},
    ])

    assert result == ""
    assert [call[0] for call in client.calls] == [
        "capture_content",
        "get_capabilities",
        "flush_memory",
    ]
    assert "backward compatible" in client.calls[0][1][2]
    assert client.calls[0][2]["metadata"]["kind"] == "hermes-context-compression"


def test_pre_compress_is_disabled_by_default(provider_and_client):
    provider, client = provider_and_client

    provider.on_pre_compress([{"role": "user", "content": "Do not capture this by default."}])

    assert client.calls == []


def test_pre_compress_filters_roles_and_redacts_secrets(provider_and_client):
    provider, client = provider_and_client
    provider._config["capture_pre_compress"] = True

    provider.on_pre_compress([
        {"role": "system", "content": "system-secret=system-value"},
        {"role": "user", "content": "Use api_key=super-secret-value for deployment."},
        {"role": "tool", "content": "tool-secret=tool-value"},
        {"role": "assistant", "content": "password=hunter2 is not persisted."},
    ])

    content = client.calls[0][1][2]
    assert "system-secret" not in content
    assert "tool-secret" not in content
    assert "super-secret-value" not in content
    assert "hunter2" not in content
    assert "[REDACTED]" in content
    assert "deployment" in content


def test_pre_compress_captures_only_new_overlapping_windows(provider_and_client):
    provider, client = provider_and_client
    provider._config["capture_pre_compress"] = True

    first_window = [
        {"role": "user", "content": "First user turn."},
        {"role": "assistant", "content": "First assistant turn."},
    ]
    second_window = [
        *first_window,
        {"role": "user", "content": "Second user turn."},
        {"role": "assistant", "content": "Second assistant turn."},
    ]
    third_window = [
        {"role": "user", "content": "Second user turn."},
        {"role": "assistant", "content": "Second assistant turn."},
        {"role": "user", "content": "Third user turn."},
        {"role": "assistant", "content": "Third assistant turn."},
    ]

    provider.on_pre_compress(first_window)
    provider.on_pre_compress(second_window)
    provider.on_pre_compress(third_window)
    provider.on_pre_compress(third_window)

    capture_calls = [call for call in client.calls if call[0] == "capture_content"]
    assert len(capture_calls) == 3
    assert "First user turn" in capture_calls[0][1][2]
    assert "Second user turn" in capture_calls[1][1][2]
    assert "First user turn" not in capture_calls[1][1][2]
    assert "Third user turn" in capture_calls[2][1][2]
    assert "Second user turn" not in capture_calls[2][1][2]
    assert len({call[1][1] for call in capture_calls}) == 3


def test_memory_write_retires_mapped_entries_for_replace_and_remove(provider_and_client):
    provider, client = provider_and_client

    provider.on_memory_write("add", "user", "The user prefers uv.")
    provider._wait_for_background()
    provider.on_memory_write(
        "replace",
        "user",
        "The user prefers rye.",
        {"old_text": "The user prefers uv."},
    )
    provider._wait_for_background()
    provider.on_memory_write(
        "remove",
        "user",
        "",
        {"old_text": "The user prefers rye."},
    )
    provider._wait_for_background()

    assert [call[0] for call in client.calls] == [
        "remember_memory",
        "search_memory",
        "retire_memory_entry",
        "remember_memory",
        "search_memory",
        "retire_memory_entry",
    ]
    assert client.calls[2][1][1]["entry_id"] == "entry-1"
    assert client.calls[5][1][1]["entry_id"] == "entry-2"


def test_memory_write_matches_partial_old_text_for_replace_and_remove(provider_and_client):
    provider, client = provider_and_client

    provider.on_memory_write("add", "user", "The user prefers uv.")
    provider._wait_for_background()
    provider.on_memory_write(
        "replace",
        "user",
        "The user prefers rye.",
        {"old_text": "prefers uv"},
    )
    provider._wait_for_background()
    provider.on_memory_write(
        "remove",
        "user",
        "",
        {"old_text": "prefers rye"},
    )
    provider._wait_for_background()

    retire_calls = [call for call in client.calls if call[0] == "retire_memory_entry"]
    assert [call[1][1]["entry_id"] for call in retire_calls] == ["entry-1", "entry-2"]
    assert [call[1][1]["memory_ref"]["revision"] for call in retire_calls] == [1, 3]


def test_memory_write_does_not_retire_unmapped_same_text(provider_and_client):
    provider, client = provider_and_client
    text = "The user prefers uv."
    client.remember_memory(
        provider._scope_id,
        kind="preference",
        text=text,
        reason="created directly in PowerContext",
    )
    client.calls.clear()

    provider.on_memory_write("remove", "user", "", {"old_text": text})
    provider._wait_for_background()

    assert [call[0] for call in client.calls] == ["search_memory"]
    assert text in client._memory_entries


def test_memory_map_refreshes_revision_after_multiple_writes(provider_and_client):
    provider, client = provider_and_client

    provider.on_memory_write("add", "user", "The user prefers uv.")
    provider._wait_for_background()
    provider.on_memory_write("add", "user", "The project uses Python.")
    provider._wait_for_background()

    provider.on_memory_write(
        "replace",
        "user",
        "The user prefers rye.",
        {"old_text": "The user prefers uv."},
    )
    provider._wait_for_background()
    provider.on_memory_write(
        "remove",
        "user",
        "",
        {"old_text": "The user prefers rye."},
    )
    provider._wait_for_background()

    retire_calls = [call for call in client.calls if call[0] == "retire_memory_entry"]
    assert [call[1][1]["memory_ref"]["revision"] for call in retire_calls] == [2, 4]


def test_memory_write_skips_replace_and_remove_without_old_text(provider_and_client):
    provider, client = provider_and_client

    provider.on_memory_write("replace", "memory", "new value")
    provider.on_memory_write("remove", "memory", "")
    provider._wait_for_background()

    assert client.calls == []


def test_memory_tools_map_to_powercontext_operations(provider_and_client):
    provider, client = provider_and_client
    citation_args = {
        "family": "memory",
        "artifact_id": "memory-1",
        "revision": 1,
        "entry_id": "entry-1",
        "entry_version_id": "entry-version-1",
    }

    search = json.loads(provider.handle_tool_call("powercontext_search_memory", {"query": "deployment"}))
    saved = json.loads(
        provider.handle_tool_call(
            "powercontext_remember",
            {"kind": "decision", "text": "Use the Hermes standard Provider interface."},
        )
    )
    read = json.loads(provider.handle_tool_call("powercontext_get_memory", citation_args))
    retired = json.loads(provider.handle_tool_call("powercontext_retire_memory", citation_args))

    assert search["hits"]
    assert saved["status"] == "remembered"
    assert read["text"] == "a memory"
    assert retired["status"] == "retired"
    assert [call[0] for call in client.calls] == [
        "search_memory",
        "remember_memory",
        "get_memory_entry",
        "retire_memory_entry",
    ]


def test_extended_tools_are_registered_and_scope_bound(provider_and_client):
    provider, client = provider_and_client

    schemas = provider.get_tool_schemas()
    assert "powercontext_list_memory_entries" in {schema["name"] for schema in schemas}

    result = json.loads(
        provider.handle_tool_call(
            "powercontext_list_memory_entries",
            {"include_inactive": True, "scope_id": "attacker-scope"},
        )
    )

    assert result["operation"] == "list_memory_entries"
    assert result["payload"] == {
        "include_inactive": True,
        "scope_id": "hermes:coder:user-7",
    }
    assert client.calls[-1] == (
        "request_operation",
        (
            "list_memory_entries",
            {"include_inactive": True, "scope_id": "hermes:coder:user-7"},
        ),
        {},
    )


def test_extended_slash_commands_dispatch_json_operations(provider_and_client):
    provider, client = provider_and_client

    result = json.loads(
        provider.handle_slash_command(
            'handoff prepare {"objective":"finish integration","evidence":[]}',
        )
    )
    stats = json.loads(provider.handle_slash_command("stats 7d"))
    help_text = provider.handle_slash_command("help")

    assert result["operation"] == "prepare_handoff"
    assert result["payload"]["scope_id"] == "hermes:coder:user-7"
    assert result["payload"]["objective"] == "finish integration"
    assert stats["operation"] == "get_stats"
    assert stats["payload"] == {"period": "7d", "scope_id": "hermes:coder:user-7"}
    assert "/pc workstream" in help_text
    assert [call[0] for call in client.calls] == ["request_operation", "request_operation"]


def test_slash_commands_parse_unwrapped_citation_json_from_readme(provider_and_client):
    provider, client = provider_and_client
    citation = client.remember_memory(
        provider._scope_id,
        kind="preference",
        text="The user prefers uv.",
        reason="seed test citation",
    )["entry"]["citation"]
    citation_json = json.dumps(citation)
    client.calls.clear()

    fetched = json.loads(provider.handle_slash_command(f"get {citation_json}"))
    revised = json.loads(
        provider.handle_slash_command(
            f'revise {citation_json} preference "The user prefers rye." "toolchain update"',
        )
    )
    retired = json.loads(provider.handle_slash_command(f'retire {citation_json} "no longer current"'))

    assert fetched["text"] == "a memory"
    assert revised == {
        "operation": "revise_memory_entry",
        "payload": {
            "citation": citation,
            "kind": "preference",
            "text": "The user prefers rye.",
            "reason": "toolchain update",
            "scope_id": provider._scope_id,
        },
    }
    assert retired["status"] == "retired"
    assert [call[0] for call in client.calls] == [
        "get_memory_entry",
        "request_operation",
        "retire_memory_entry",
    ]


def test_workstream_binding_is_shared_with_hermes_scope(tmp_path, hermes_modules, monkeypatch):
    provider_module, _cli_module = hermes_modules
    git_directory = tmp_path / ".git"
    state_path = git_directory / "powercontext" / "codex-workspace.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(
        json.dumps({
            "schema": "powercontext.codex-workspace.v1",
            "scope_id": "git:example/project",
        }),
        encoding="utf-8",
    )
    workstream_module = importlib.import_module("plugins.powercontext.workstream")
    monkeypatch.setattr(workstream_module, "git_value", lambda _cwd, *_args: str(git_directory))
    provider = provider_module.PowerContextMemoryProvider(
        {"scope_id": "hermes:{profile}:{user_id}"},
        client_factory=lambda _config: FakeClient(),
    )
    provider.initialize(
        "session-1",
        hermes_home=str(tmp_path / "hermes"),
        cwd=str(tmp_path),
        agent_identity="coder",
        user_id="user-7",
    )

    status = json.loads(provider.handle_slash_command("workstream status"))
    assert status["bound_scope_id"] == "git:example/project"
    provider.shutdown()


def test_workstream_clear_restores_default_scope(tmp_path, hermes_modules, monkeypatch):
    provider_module, _cli_module = hermes_modules
    git_directory = tmp_path / ".git"
    git_directory.mkdir()
    workstream_module = importlib.import_module("plugins.powercontext.workstream")
    monkeypatch.setattr(workstream_module, "git_value", lambda _cwd, *_args: str(git_directory))

    client = FakeClient()
    provider = provider_module.PowerContextMemoryProvider(
        {"scope_id": "hermes:{profile}:{user_id}"},
        client_factory=lambda _config: client,
    )
    provider.initialize(
        "session-1",
        hermes_home=str(tmp_path / "hermes"),
        cwd=str(tmp_path),
        agent_identity="coder",
        user_id="user-7",
    )
    default_scope_id = provider._scope_id

    try:
        bound = json.loads(provider.handle_slash_command("workstream bind shared:scope"))
        assert bound["scope_id"] == "shared:scope"

        cleared = json.loads(provider.handle_slash_command("workstream clear"))
        assert cleared["status"] == "cleared"

        status = json.loads(provider.handle_slash_command("workstream status"))
        assert status["bound_scope_id"] is None
        assert status["active_scope_id"] == default_scope_id

        provider.handle_slash_command("search uv")
        assert client.calls[-1][0] == "search_memory"
        assert client.calls[-1][1][0] == default_scope_id
    finally:
        provider.shutdown()


def test_workstream_bind_isolates_queued_background_work(tmp_path, hermes_modules, monkeypatch):
    provider_module, _cli_module = hermes_modules
    git_directory = tmp_path / ".git"
    git_directory.mkdir()
    workstream_module = importlib.import_module("plugins.powercontext.workstream")
    monkeypatch.setattr(workstream_module, "git_value", lambda _cwd, *_args: str(git_directory))

    client = FakeClient()
    provider = provider_module.PowerContextMemoryProvider(
        {"scope_id": "hermes:{profile}:{user_id}", "shutdown_timeout": 0.01},
        client_factory=lambda _config: client,
    )
    provider.initialize(
        "session-1",
        hermes_home=str(tmp_path / "hermes"),
        cwd=str(tmp_path),
        agent_identity="coder",
        user_id="user-7",
    )
    release = threading.Event()
    started = threading.Event()
    old_scope_id = "hermes:coder:user-7"

    def blocked_prepare(*args: Any, **kwargs: Any) -> dict[str, Any]:
        scope_id = args[0]
        query = args[1]
        client.calls.append(("prepare_context", (scope_id, query), {"max_bytes": kwargs["max_bytes"]}))
        started.set()
        release.wait(timeout=2)
        return {"status": "ready", "content": f"context for {scope_id}"}

    monkeypatch.setattr(client, "prepare_context", blocked_prepare)
    try:
        provider.queue_prefetch("same query")
        assert started.wait(timeout=1)
        provider.sync_turn("old user", "old assistant")

        result = json.loads(provider.handle_slash_command("workstream bind new:scope"))
        assert result["status"] == "bound"

        release.set()
        provider._config["shutdown_timeout"] = 1.0
        provider._wait_for_background()

        capture_calls = [call for call in client.calls if call[0] == "capture_content"]
        assert capture_calls == []

        recalled = provider.prefetch("same query")
        assert "context for new:scope" in recalled
        prepare_calls = [call for call in client.calls if call[0] == "prepare_context"]
        assert [call[1][0] for call in prepare_calls] == [old_scope_id, "new:scope"]
    finally:
        release.set()
        provider.shutdown()


def test_backend_failure_fails_open(provider_and_client):
    provider, client = provider_and_client

    def failed_prepare(*args, **kwargs):
        from plugins.powercontext.client import PowerContextTransportError  # ty: ignore[unresolved-import]

        raise PowerContextTransportError("offline")

    client.prepare_context = failed_prepare

    assert provider.prefetch("query") == ""


def test_cli_registers_provider_commands(hermes_modules):
    _provider_module, cli_module = hermes_modules
    parser = argparse.ArgumentParser()
    root = parser.add_subparsers(dest="provider")
    provider = root.add_parser("powercontext")
    cli_module.register_cli(provider)

    args = parser.parse_args(["powercontext", "search", "deployment", "--limit", "3"])

    assert args.powercontext_command == "search"
    assert args.query == "deployment"
    assert args.limit == 3
    assert callable(args.func)


def test_http_client_dispatches_operation_paths_and_get_query(hermes_modules):
    provider_module, _cli_module = hermes_modules
    requests = []

    class Response:
        status = 200

        def read(self, _limit):
            return b'{"ok":true}'

    def transport(request, _timeout):
        requests.append(request)
        return Response()

    client = provider_module.PowerContextClient(
        "http://powercontext.test:8000",
        transport=transport,
    )
    result = client.request_operation("get_stats", {"scope_id": "hermes:test", "period": "7d"})

    assert result == {"ok": True}
    assert requests[0].full_url == "http://powercontext.test:8000/v1/stats?scope_id=hermes%3Atest&period=7d"
    assert requests[0].method == "GET"
