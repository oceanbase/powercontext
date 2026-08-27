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

"""Scope resolution tests for the standalone LangChain package."""

import pytest
from powercontext_langchain import MissingScopeError, resolve_scope_id
from powercontext_langchain.scope import normalize_git_remote


def test_missing_scope_names_langchain_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("powercontext_langchain.scope._git_remote", lambda _cwd: None)

    with pytest.raises(MissingScopeError, match="POWERCONTEXT_LANGCHAIN_SCOPE_ID"):
        resolve_scope_id(None, cwd="/irrelevant")


@pytest.mark.parametrize(
    ("remote", "expected"),
    [
        ("git@github.com:acme/api.git", "github.com/acme/api"),
        ("https://credential@github.com/acme/api.git", "github.com/acme/api"),
        ("ssh://git@example.com:2222/acme/api.git", "example.com:2222/acme/api"),
    ],
)
def test_normalize_git_remote_removes_credentials(remote: str, expected: str) -> None:
    assert normalize_git_remote(remote) == expected


@pytest.mark.parametrize("remote", [r"C:\work\tenant-repo.git", "C:/work/tenant-repo.git"])
def test_resolve_scope_id_rejects_windows_local_remote(monkeypatch: pytest.MonkeyPatch, remote: str) -> None:
    monkeypatch.setattr("powercontext_langchain.scope._git_remote", lambda _cwd: remote)

    with pytest.raises(MissingScopeError):
        resolve_scope_id(None, cwd="/irrelevant")
