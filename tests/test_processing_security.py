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

"""Only deployment-owned authorization can be reconstructed in a child."""

import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.runtime.composition import BuiltinConfigurationError
from powercontext.builtin.runtime.config import InferenceConfig
from powercontext.server.authz import AccessControlService, PrincipalRef
from powercontext.server.authz.repository import ACCESS_TABLES, RelationalAccessRepository
from powercontext.server.authz.service import BuiltinAuthorizationProvider
from powercontext.server.cli import _run_background_async
from powercontext.server.factory import create_server_app
from powercontext.server.processing_security import WorkerSecuritySpec, build_worker_security
from powercontext.server.settings import AccessControlConfig, BearerAuthConfig, ServerSettings


class CustomAuthorizationProvider(BuiltinAuthorizationProvider):
    """An injected subclass may impose rules the standard child cannot rebuild."""


def test_worker_identity_prefers_explicit_background_principal_and_excludes_credentials():
    settings = ServerSettings(
        auth=BearerAuthConfig(token=SecretStr("do-not-serialize-this-token")),
        access=AccessControlConfig(mode="enforced", background_principal_id="worker", deployment_id="test"),
    )
    legacy = PrincipalRef(type="service", id="server-token")
    data = build_worker_security(settings, None, legacy_static_principal=legacy)
    assert data is not None
    spec = WorkerSecuritySpec.model_validate(data)
    assert spec.principal.id == "worker"
    assert spec.deployment_id == "test"
    assert spec.static_preset is False
    assert "do-not-serialize-this-token" not in json.dumps(data)
    assert "worker" not in repr(spec)


def test_injected_authorization_fails_before_background_dispatch_but_not_for_sync_only():
    settings = ServerSettings(access=AccessControlConfig(mode="enforced", background_principal_id="worker"))
    with pytest.raises(BuiltinConfigurationError, match="reconstructible Authorization Provider"):
        build_worker_security(settings, None, injected=True)
    assert build_worker_security(settings, None, injected=True, enabled=False) is None
    assert build_worker_security(ServerSettings(), None, injected=True) is None


def test_authorization_subclass_is_not_silently_replaced_by_builtin_provider(tmp_path):
    async def scenario():
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'access.db'}")
        settings = ServerSettings(access=AccessControlConfig(mode="enforced", background_principal_id="worker"))
        async with SQLiteProfile.open(config, tables=ACCESS_TABLES) as profile:
            repository = RelationalAccessRepository(profile.database)
            access = AccessControlService(
                CustomAuthorizationProvider(repository), relationships=repository, audit=repository
            )
            with pytest.raises(BuiltinConfigurationError, match="reconstructible Authorization Provider"):
                build_worker_security(settings, access)
            standard = AccessControlService(
                BuiltinAuthorizationProvider(repository), relationships=repository, audit=repository
            )
            assert build_worker_security(settings, standard) is not None

    asyncio.run(scenario())


def test_server_rejects_injected_authorization_before_registering_actual_background_workers(tmp_path):
    async def scenario():
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'server.db'}")
        settings = ServerSettings(
            database=config,
            auth=BearerAuthConfig(token=SecretStr("test-token")),
            access=AccessControlConfig(mode="enforced", background_principal_id="worker"),
            inference=InferenceConfig(generation_model="test"),
        )
        async with SQLiteProfile.open(config, tables=ACCESS_TABLES) as profile:
            repository = RelationalAccessRepository(profile.database)
            access = AccessControlService(
                BuiltinAuthorizationProvider(repository), relationships=repository, audit=repository
            )
            app = create_server_app(settings=settings, access_control=access)
            with pytest.raises(BuiltinConfigurationError, match="reconstructible Authorization Provider"):
                async with app.router.lifespan_context(app):
                    pass
            settings = settings.model_copy(update={"inference": InferenceConfig()})
            app = create_server_app(settings=settings, access_control=access)
            async with app.router.lifespan_context(app):
                assert app.state.application.artifact_processing_supervisor is None

    asyncio.run(scenario())


def test_background_cli_preserves_deployment_identity_when_building_the_worker_spec(monkeypatch):
    async def scenario():
        settings = ServerSettings(
            access=AccessControlConfig(
                mode="enforced", background_principal_id="background-worker", deployment_id="test"
            )
        )
        observed = {}
        stop_callbacks = []
        loop = asyncio.get_running_loop()
        monkeypatch.setattr(loop, "add_signal_handler", lambda _signal, callback: stop_callbacks.append(callback))
        monkeypatch.setattr(loop, "remove_signal_handler", lambda _signal: True)

        @asynccontextmanager
        async def access_control(_database, **kwargs):
            observed["access"] = kwargs
            yield None

        @asynccontextmanager
        async def runtime(_config, **kwargs):
            observed["runtime"] = kwargs
            stop_callbacks[0]()
            yield None

        monkeypatch.setattr("powercontext.server.cli.open_builtin_access_control", access_control)
        monkeypatch.setattr("powercontext.server.cli.open_builtin_runtime", runtime)
        await _run_background_async(settings, SimpleNamespace(instrumentation=None))
        security = observed["runtime"]["worker_security"]
        assert security["principal"]["id"] == "background-worker"
        assert security["deployment_id"] == observed["access"]["deployment_id"] == "test"
        assert security["static_preset"] is False

    asyncio.run(scenario())
