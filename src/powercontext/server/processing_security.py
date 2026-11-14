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

"""Serialize the processing-child authorization identity from Server settings."""

from __future__ import annotations

from typing import Any

from powercontext.builtin.authz import AccessControlService, PrincipalRef
from powercontext.builtin.authz.repository import RelationalAccessRepository
from powercontext.builtin.authz.service import BuiltinAuthorizationProvider
from powercontext.builtin.authz.worker import WorkerSecuritySpec
from powercontext.builtin.runtime.composition import BuiltinConfigurationError
from powercontext.server.settings import ServerSettings


def build_worker_security(
    settings: ServerSettings,
    access: AccessControlService | None,
    *,
    legacy_static_principal: PrincipalRef | None = None,
    enabled: bool = True,
    injected: bool = False,
) -> dict[str, Any] | None:
    """Fail before dispatch when an enforced provider cannot be reconstructed."""

    if not enabled or settings.access.mode == "disabled":
        return None
    if injected or (
        access is not None
        and (
            type(access.provider) is not BuiltinAuthorizationProvider
            or type(access.relationships) is not RelationalAccessRepository
            or access.audit is not access.relationships
        )
    ):
        raise BuiltinConfigurationError("artifact-processing-worker-authorization-provider")
    if settings.access.background_principal_id is not None:
        principal = PrincipalRef(
            type="service",
            id=settings.access.background_principal_id,
            description=settings.access.background_principal_description,
        )
    elif legacy_static_principal is not None:
        principal = legacy_static_principal
    else:
        raise ValueError("scheduled processing in enforced mode requires ACCESS_BACKGROUND_PRINCIPAL_ID")  # noqa: TRY003
    return WorkerSecuritySpec(
        principal=principal,
        deployment_id=settings.access.deployment_id,
        static_preset=principal == legacy_static_principal,
    ).model_dump(mode="json")
