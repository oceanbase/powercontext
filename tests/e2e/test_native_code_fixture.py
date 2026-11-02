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

"""Native Code fixture scopes remain usable when the HTTP Server opens their database."""

import asyncio
import os

import pytest

from powercontext.builtin.code import CodeConfig, CodeRepositoryConfig, CodeService
from powercontext.builtin.runtime import InferenceConfig, RuntimeConfig
from powercontext.client import PowerContextClient
from powercontext.server.settings import McpConfig, ServerSettings
from tests.e2e.real_experience_skill.harness import _start_configured_server
from tests.e2e.real_experience_skill.test_native_code import _code_context, _repository, _scope

pytest.importorskip("tree_sitter")
pytest.importorskip("tree_sitter_python")

pytestmark = pytest.mark.skipif(
    os.name != "posix", reason="native code indexing requires POSIX file locks and resource limits"
)


@pytest.mark.parametrize("migration_backend", ["sqlite", "oceanbase"], indirect=True)
def test_native_code_seed_can_be_served(migration_backend, monkeypatch):
    root = migration_backend.directory
    monkeypatch.chdir(root)
    for key in tuple(os.environ):
        if key.startswith("POWERCONTEXT_SERVER_"):
            monkeypatch.delenv(key)
    settings = ServerSettings(
        database=migration_backend.config,
        inference=InferenceConfig(),
        runtime=RuntimeConfig(),
        mcp=McpConfig(enabled=False),
    )
    scope_id = asyncio.run(_scope(settings, root))
    repository = root / "repository"
    _repository(repository, "REL-storage")
    code = CodeConfig(
        enabled=True,
        cache_dir=root / "code-cache",
        repositories={scope_id: CodeRepositoryConfig(root=repository)},
    )
    CodeService(code).index(scope_id)
    server = _start_configured_server(settings.model_copy(update={"code": code}), root / "server-scheduler.db")

    async def scenario():
        async with PowerContextClient(server.base_url) as client:
            scope = await client.get_scope(scope_id)
            assert scope.scope_id == scope_id
        report = {}
        await _code_context(server.base_url, None, scope_id, report)
        assert report["code_only_context"]["code_present"]

    try:
        asyncio.run(scenario())
    finally:
        server.stop()
