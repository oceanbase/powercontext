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

from __future__ import annotations

import pytest
from pydantic import SecretStr, ValidationError

from powercontext.builtin.persistence.oceanbase import OceanBaseConfig
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime.config import (
    BuiltinConfig,
    CoordinationConfig,
    DeploymentConfig,
    WorkerConfig,
)


def test_single_node_all_is_the_backwards_compatible_default() -> None:
    config = BuiltinConfig(database=SQLiteConfig())

    assert config.deployment.mode == "single_node"
    assert config.deployment.role == "all"
    assert config.worker.concurrency == 4
    assert config.worker.lease_seconds == 120


def test_distributed_requires_oceanbase_and_one_process_role() -> None:
    with pytest.raises(ValidationError, match="distributed deployment requires OceanBase"):
        BuiltinConfig(
            database=SQLiteConfig(),
            deployment=DeploymentConfig(mode="distributed", role="api", id="api-a"),
        )

    with pytest.raises(ValidationError, match="distributed deployment role must be"):
        BuiltinConfig(
            database=OceanBaseConfig(
                url=SecretStr("mysql+aoceanbase://root@localhost:2881/powercontext?charset=utf8mb4")
            ),
            deployment=DeploymentConfig(mode="distributed", role="all", id="all-a"),
        )


def test_worker_heartbeat_and_shutdown_must_fit_inside_the_lease() -> None:
    with pytest.raises(ValidationError, match="heartbeat_seconds must be less than one third"):
        WorkerConfig(lease_seconds=120, heartbeat_seconds=40)

    with pytest.raises(ValidationError, match="shutdown_grace_seconds must be less than lease_seconds"):
        WorkerConfig(lease_seconds=120, shutdown_grace_seconds=120)


def test_scheduler_emits_only_the_supported_payload_version() -> None:
    with pytest.raises(ValidationError):
        CoordinationConfig.model_validate({"emit_payload_version": 2})
