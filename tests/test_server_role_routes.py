# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""The deployment role gates the actual factory route table, not only composition."""

import pytest

from powercontext.builtin.persistence.oceanbase.profile import OceanBaseConfig
from powercontext.builtin.runtime.config import DeploymentConfig
from powercontext.server.factory import create_server_app
from powercontext.server.settings import ServerSettings


def _settings(role: str, **overrides) -> ServerSettings:
    values = {
        "deployment": DeploymentConfig(mode="distributed", role=role, id=f"node-{role}"),
        "database": OceanBaseConfig(
            url="mysql+aoceanbase://user:pass@localhost:2881/powercontext?charset=utf8mb4"
        ),
        # A distributed Worker validates a generation model at composition time;
        # the route table assertions below never open the runtime.
        "inference": {"generation_model": "stub:model"},
        "mcp": {"enabled": False},
    }
    values.update(overrides)
    return ServerSettings(**values)


def _paths(app) -> set[str]:
    return {route.path for route in app.routes}


@pytest.mark.parametrize("role", ["scheduler", "worker"])
def test_distributed_roles_expose_only_the_health_metrics_boundary(role):
    app = create_server_app(settings=_settings(role))
    paths = _paths(app)
    assert "/health/live" in paths
    assert "/health/ready" in paths
    assert "/metrics" in paths
    data_plane = [path for path in paths if path.startswith("/v1/")]
    assert data_plane == []
    assert "/openapi.json" not in paths
    assert "/docs" not in paths


def test_distributed_api_keeps_the_data_plane():
    app = create_server_app(settings=_settings("api"))
    paths = _paths(app)
    assert "/health/live" in paths
    assert "/v1/memory/flush" in paths
    assert "/v1/scopes/{scope_id}/operations" in paths


def test_single_node_keeps_the_data_plane():
    settings = ServerSettings(
        database=OceanBaseConfig(url="mysql+aoceanbase://user:pass@localhost:2881/powercontext?charset=utf8mb4"),
        mcp={"enabled": False},
    )
    paths = _paths(create_server_app(settings=settings))
    assert "/v1/memory/flush" in paths
    assert "/v1/scopes/{scope_id}/operations" in paths


def test_health_boundary_roles_reject_dashboard_and_mcp():
    with pytest.raises(ValueError, match="health and metrics"):
        create_server_app(settings=_settings("worker", mcp={"enabled": True}))
