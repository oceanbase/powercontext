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

import hashlib
import os
import subprocess
from pathlib import Path
from shutil import which
from types import TracebackType
from typing import Self

import pytest
from click import unstyle
from typer.testing import CliRunner

import powercontext.client.cli as client_cli
from powercontext.cli.app import create_cli
from powercontext.http import (
    ClearScopeBindingRequest,
    ClearScopeBindingResponse,
    CreateScopeRequest,
    ResolveScopeBindingRequest,
    ScopeBinding,
    ScopeDescriptor,
    ScopePage,
    SetDefaultScopeRequest,
    SetScopeBindingRequest,
    UpdateScopeRequest,
)


def _descriptor(scope_id: str = "scp_test", **overrides: object) -> ScopeDescriptor:
    fields: dict[str, object] = {
        "scope_id": scope_id,
        "title": "PowerContext",
        "summary": "memory for powercontext",
        "parent_scope_id": None,
        "context_references": [],
        "external_references": [],
        "version": 1,
    }
    return ScopeDescriptor.model_validate({**fields, **overrides})


def _workspace_external_id(cwd: Path) -> str:
    return hashlib.sha256(os.path.abspath(cwd).encode("utf-8")).hexdigest()


class _RecordingClient:
    response: object

    def __init__(self) -> None:
        self.calls: list[object] = []

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None

    def _record(self, request: object) -> object:
        self.calls.append(request)
        return self.response


def _recording_type(response: object) -> type:
    class Client(_RecordingClient):
        async def create_scope(self, request: CreateScopeRequest) -> object:
            return self._record(request)

        async def list_scopes(self, **kwargs: object) -> object:
            return self._record(kwargs)

        async def get_scope(self, scope_id: str) -> object:
            return self._record(scope_id)

        async def update_scope(self, scope_id: str, request: UpdateScopeRequest) -> object:
            return self._record((scope_id, request))

        async def get_default_scope(self) -> object:
            return self._record(None)

        async def set_default_scope(self, request: SetDefaultScopeRequest) -> object:
            return self._record(request)

        async def resolve_scope_binding(self, request: ResolveScopeBindingRequest) -> object:
            return self._record(request)

        async def set_scope_binding(self, request: SetScopeBindingRequest) -> object:
            return self._record(request)

        async def clear_scope_binding(self, request: ClearScopeBindingRequest) -> object:
            return self._record(request)

    Client.response = response
    return Client


def _invoke(monkeypatch: pytest.MonkeyPatch, response: object, arguments: list[str]) -> tuple[_RecordingClient, str]:
    client = _recording_type(response)()
    monkeypatch.setattr(client_cli, "PowerContextClient", lambda *_args, **_kwargs: client)
    result = CliRunner().invoke(create_cli([]), arguments)
    assert result.exit_code == 0, unstyle(result.output)
    return client, unstyle(result.output)


def test_scope_create_sends_explicit_idempotency_key_and_prints_next_step(monkeypatch: pytest.MonkeyPatch) -> None:
    client, output = _invoke(
        monkeypatch,
        _descriptor("scp_created"),
        [
            "scope",
            "create",
            "--title",
            "PowerContext",
            "--summary",
            "memory",
            "--idempotency-key",
            "powercontext-repo",
        ],
    )
    request = client.calls[0]
    assert isinstance(request, CreateScopeRequest)
    assert request.title == "PowerContext"
    assert request.idempotency_key == "powercontext-repo"
    assert "scp_created" in output
    assert "SCOPE_ID" in output


def test_scope_create_generates_an_idempotency_key_when_omitted(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _invoke(
        monkeypatch,
        _descriptor(),
        ["scope", "create", "--title", "PowerContext", "--summary", "memory"],
    )
    request = client.calls[0]
    assert isinstance(request, CreateScopeRequest)
    assert request.idempotency_key


def test_scope_create_rejects_malformed_external_references(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _recording_type(_descriptor())()
    monkeypatch.setattr(client_cli, "PowerContextClient", lambda *_args, **_kwargs: client)
    result = CliRunner().invoke(
        create_cli([]),
        [
            "scope",
            "create",
            "--title",
            "PowerContext",
            "--summary",
            "memory",
            "--external-reference",
            "git:",
        ],
    )
    assert result.exit_code != 0
    assert "expects kind=value" in unstyle(result.output)
    assert client.calls == []


def test_scope_create_sends_wellformed_external_references(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _invoke(
        monkeypatch,
        _descriptor(external_references=[{"kind": "git", "value": "github.com/oceanbase/powercontext"}]),
        [
            "scope",
            "create",
            "--title",
            "PowerContext",
            "--summary",
            "memory",
            "--external-reference",
            "git=github.com/oceanbase/powercontext",
        ],
    )
    request = client.calls[0]
    assert isinstance(request, CreateScopeRequest)
    assert [f"{reference.kind}={reference.value}" for reference in request.external_references] == [
        "git=github.com/oceanbase/powercontext"
    ]


def test_scope_resolve_mirrors_the_plugin_binding_keys(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    workspace = tmp_path / "worktree"
    client, output = _invoke(
        monkeypatch,
        _descriptor("scp_resolved"),
        [
            "scope",
            "resolve",
            "--cwd",
            str(workspace),
            "--session-id",
            "ses_1",
            "--no-allow-default",
        ],
    )
    request = client.calls[0]
    assert isinstance(request, ResolveScopeBindingRequest)
    assert request.allow_default is False
    assert request.explicit_scope_id is None
    assert [(key.integration, key.kind, key.external_id) for key in request.binding_keys] == [
        ("opencode", "session", "ses_1"),
        ("opencode", "workspace", _workspace_external_id(workspace)),
    ]
    assert "scp_resolved" in output


def test_scope_resolve_passes_the_explicit_scope_id(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _invoke(
        monkeypatch,
        _descriptor(),
        ["scope", "resolve", "--scope-id", "git:github.com/oceanbase/powercontext"],
    )
    request = client.calls[0]
    assert isinstance(request, ResolveScopeBindingRequest)
    assert request.explicit_scope_id == "git:github.com/oceanbase/powercontext"


def test_scope_update_forwards_the_expected_version(monkeypatch: pytest.MonkeyPatch) -> None:
    client, output = _invoke(
        monkeypatch,
        _descriptor("scp_moved", version=2),
        [
            "scope",
            "update",
            "scp_moved",
            "--title",
            "Renamed",
            "--summary",
            "updated summary",
            "--expected-version",
            "2",
        ],
    )
    call = client.calls[-1]
    assert call == ("scp_moved", UpdateScopeRequest(expected_version=2, title="Renamed", summary="updated summary"))
    assert "scp_moved" in output


def test_scope_list_prints_every_descriptor_and_the_cursor(monkeypatch: pytest.MonkeyPatch) -> None:
    page = ScopePage(items=[_descriptor("scp_a"), _descriptor("scp_b")], next_cursor="cursor-1")
    client, output = _invoke(monkeypatch, page, ["scope", "list"])
    assert client.calls[0] == {"query": None, "query_field": None, "limit": 50, "cursor": None}
    assert "scp_a" in output
    assert "scp_b" in output
    assert "Next cursor: cursor-1" in output


def test_scope_set_default_forwards_the_exact_scope_id(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _invoke(monkeypatch, _descriptor(), ["scope", "set-default", "scp_default"])
    request = client.calls[0]
    assert isinstance(request, SetDefaultScopeRequest)
    assert request.scope_id == "scp_default"


def test_scope_binding_set_prints_the_bound_key(monkeypatch: pytest.MonkeyPatch) -> None:
    client, output = _invoke(
        monkeypatch,
        ScopeBinding.model_validate({
            "key": {"integration": "opencode", "kind": "workspace", "external_id": "abc"},
            "scope_id": "scp_bound",
        }),
        ["scope", "binding", "set", "scp_bound", "--kind", "workspace", "--external-id", "abc"],
    )
    request = client.calls[0]
    assert isinstance(request, SetScopeBindingRequest)
    key = request.root.key
    assert key.integration == "opencode"
    assert key.kind == "workspace"
    assert key.external_id == "abc"
    assert "-> scp_bound" in output


def test_scope_binding_clear_reports_the_outcome(monkeypatch: pytest.MonkeyPatch) -> None:
    _client, output = _invoke(
        monkeypatch,
        ClearScopeBindingResponse(cleared=False),
        [
            "scope",
            "binding",
            "clear",
            "--kind",
            "workspace",
            "--external-id",
            "abc",
            "--integration",
            "codex",
        ],
    )
    assert "Binding was not set." in output


def test_scope_binding_clear_reports_a_cleared_binding(monkeypatch: pytest.MonkeyPatch) -> None:
    _client, output = _invoke(
        monkeypatch,
        ClearScopeBindingResponse(cleared=True),
        ["scope", "binding", "clear", "--kind", "session", "--external-id", "ses_1"],
    )
    assert "Binding cleared." in output


@pytest.mark.parametrize("current_version", [1, 2])
def test_scope_update_preserves_relationships(monkeypatch: pytest.MonkeyPatch, current_version: int) -> None:
    descriptor = _descriptor(
        version=current_version,
        parent_scope_id="scp_parent",
        context_references=["scp_related"],
        external_references=[{"kind": "git", "value": "example/repo"}],
    )
    client, _ = _invoke(
        monkeypatch,
        descriptor,
        [
            "scope",
            "update",
            "scp_test",
            "--title",
            "Renamed",
            "--summary",
            "Updated",
            "--expected-version",
            "1",
        ],
    )
    call = client.calls[-1]
    assert isinstance(call, tuple)
    _, request = call
    assert isinstance(request, UpdateScopeRequest)
    assert request.expected_version == 1
    assert request.parent_scope_id == descriptor.parent_scope_id
    assert request.context_references == descriptor.context_references
    assert request.external_references == descriptor.external_references


@pytest.mark.parametrize("use_symlink", [False, True])
def test_codex_scope_resolve_uses_canonical_repository_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    use_symlink: bool,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    executable = which("git")
    assert executable is not None
    subprocess.run([executable, "init", str(repo)], check=True, capture_output=True)
    workspace = repo / "src"
    workspace.mkdir()
    if use_symlink:
        link = tmp_path / "link"
        try:
            link.symlink_to(workspace, target_is_directory=True)
        except OSError:
            pytest.skip("Host cannot create directory symlinks")
        workspace = link
    client, _ = _invoke(
        monkeypatch,
        _descriptor(),
        [
            "scope",
            "resolve",
            "--integration",
            "codex",
            "--cwd",
            str(workspace),
            "--no-allow-default",
        ],
    )
    request = client.calls[0]
    assert isinstance(request, ResolveScopeBindingRequest)
    assert request.binding_keys[0].external_id == hashlib.sha256(os.fsencode(repo.resolve())).hexdigest()


@pytest.mark.parametrize("integration", ["opencode", "pi", "hermes", "kimi-code", "codex", "claude-code"])
def test_scope_resolve_non_git_symlink_matches_host_rules(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    integration: str,
) -> None:
    directory = tmp_path / "directory"
    directory.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(directory, target_is_directory=True)
    except OSError:
        pytest.skip("Host cannot create directory symlinks")
    client, _ = _invoke(
        monkeypatch,
        _descriptor(),
        [
            "scope",
            "resolve",
            "--integration",
            integration,
            "--cwd",
            str(link),
        ],
    )
    request = client.calls[0]
    assert isinstance(request, ResolveScopeBindingRequest)
    expected = os.path.abspath(link) if integration in {"opencode", "pi"} else str(directory.resolve())
    assert request.binding_keys[0].external_id == hashlib.sha256(os.fsencode(expected)).hexdigest()


def test_scope_resolve_rejects_unknown_workspace_rules(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _recording_type(_descriptor())()
    monkeypatch.setattr(client_cli, "PowerContextClient", lambda *_args, **_kwargs: client)
    result = CliRunner().invoke(create_cli([]), ["scope", "resolve", "--integration", "unknown-host"])
    assert result.exit_code != 0
    assert "Unsupported workspace integration" in unstyle(result.output)
    assert client.calls == []


@pytest.mark.parametrize("integration", ["zcode", "bub", "codex"])
def test_scope_resolve_explicit_id_does_not_require_workspace_rules(
    monkeypatch: pytest.MonkeyPatch,
    integration: str,
) -> None:
    client, _ = _invoke(
        monkeypatch,
        _descriptor(),
        [
            "scope",
            "resolve",
            "--scope-id",
            "scp_test",
            "--integration",
            integration,
            "--session-id",
            "session-1",
            "--no-allow-default",
        ],
    )
    request = client.calls[0]
    assert isinstance(request, ResolveScopeBindingRequest)
    assert request.explicit_scope_id == "scp_test"
    assert request.binding_keys == []
    assert request.allow_default is False


@pytest.mark.parametrize("arguments", [["--query", "Example"], ["--query-field", "title"]])
def test_scope_list_requires_query_and_field_together(monkeypatch: pytest.MonkeyPatch, arguments: list[str]) -> None:
    client = _recording_type(ScopePage(items=[]))()
    monkeypatch.setattr(client_cli, "PowerContextClient", lambda *_args, **_kwargs: client)
    result = CliRunner().invoke(create_cli([]), ["scope", "list", *arguments])
    assert result.exit_code == 2
    assert "--query and --query-field must be supplied together" in unstyle(result.output)
    assert client.calls == []


def test_scope_list_forwards_query_with_field(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _invoke(
        monkeypatch,
        ScopePage(items=[]),
        [
            "scope",
            "list",
            "--query",
            "Example",
            "--query-field",
            "title",
        ],
    )
    assert client.calls[0] == {"query": "Example", "query_field": "title", "limit": 50, "cursor": None}
