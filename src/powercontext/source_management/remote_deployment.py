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

"""Load a collect instance on its execution machine using existing Server identities."""

from __future__ import annotations

import hashlib
import json
import secrets
import socket
import time
from contextlib import ExitStack

import httpx

from powercontext.source_management.bootstrap import _allowed, _grant, _principal, request
from powercontext.source_management.config import Endpoint, RuntimeConfig, configuration_digest, secret
from powercontext.source_management.durable import RuntimeOwner
from powercontext.source_management.loading import load_runtime_plugin
from powercontext.source_management.models import InstanceRegistration, ManagementError
from powercontext.source_management.service import launch, protected_write
from powercontext.sources import manifest_for_definition


def load(path, local, *, update=False):
    """Register, authorize and start locally without changing remote authentication configuration."""
    root = path.expanduser().resolve(strict=True).parent
    deployment = local.deployment
    state = (root / deployment.state_dir).resolve()
    lock = RuntimeOwner(state / "deployment-lock")
    try:
        with ExitStack() as updating:
            if update:
                updating.callback(RuntimeOwner(state).close)
            return _load(root, state, local, deployment, update, updating)
    finally:
        lock.close()


def _load(root, state, local, deployment, update, updating):  # noqa: C901 - validate identities before registering or starting
    admin_token = secret(root / deployment.server.credential_file)
    execution_file = root / deployment.execution_credential_file
    execution_token = secret(execution_file)
    instance_file = state / "instance.token"
    if not instance_file.exists():
        protected_write(instance_file, secrets.token_urlsafe(32), immutable=True)
    aliases = {key: (root / value).resolve() for key, value in local.secrets.items()}
    config = RuntimeConfig(
        deployment_id=deployment.deployment_id,
        instance_id=local.id,
        plugin=local.plugin,
        plugin_dir=root,
        config=local.config,
        secrets=aliases,
        management=Endpoint(url=deployment.management_url, credential_file=instance_file),
        server=Endpoint(url=deployment.server.url, credential_file=execution_file),
        state_dir=state,
        execution_location=socket.gethostname() + ":" + str(root),
        configuration_digest=configuration_digest(local.plugin, local.config, aliases),
    )
    runtime_path = state / "runtime-config.json"
    if not update and runtime_path.exists() and RuntimeConfig.model_validate_json(runtime_path.read_text()) != config:
        raise ManagementError(
            "configuration-conflict", "Stop the instance and its Runtime, then use source load --update"
        )
    registration_path = state / "registration.json"
    previous = json.loads(registration_path.read_text()) if registration_path.exists() else None
    scopes = (
        tuple(previous["scope_ids"])
        if previous is not None and not update
        else tuple(dict.fromkeys((*local.scope_ids, *(target.scope_id for target in local.targets))))
    )
    with (
        httpx.Client(
            base_url=deployment.server.url, headers={"Authorization": "Bearer " + admin_token}, timeout=65
        ) as admin,
        httpx.Client(
            base_url=deployment.server.url, headers={"Authorization": "Bearer " + execution_token}, timeout=20
        ) as execution,
    ):
        admin_identity, identity = _principal(admin), _principal(execution)
        server = {"type": "server", "deployment_id": deployment.deployment_id}
        if not _allowed(admin, "server.admin", server):
            raise ManagementError("deployment-forbidden", "Loading a collect instance requires server.admin")
        if admin_identity == identity or _allowed(execution, "server.admin", server):
            raise ManagementError("credential-separation", "The Runtime requires a separate non-administrator identity")
        plugin = load_runtime_plugin(config)
        for definition in plugin.registry.definitions:
            manifest = manifest_for_definition(definition).model_dump(mode="json", by_alias=True)
            registered = request(admin, "POST", "/v1/source-definitions/register", {"manifest": manifest})
            if registered != manifest:
                raise ManagementError("manifest-mismatch", "Server returned a different Definition manifest")
        for scope in scopes:
            _grant(admin, identity, scope)
            if any(
                not _allowed(execution, action, {"type": "scope", "scope_id": scope})
                for action in ("scope.read", "scope.contribute")
            ):
                raise ManagementError("execution-forbidden", "Execution identity cannot access the configured Scope")
        registration = InstanceRegistration(
            instance_id=local.id,
            credential_digest=hashlib.sha256(secret(instance_file).encode()).hexdigest(),
            execution_location=config.execution_location,
            scope_ids=scopes,
            public_principals=local.public_principals,
            default_capabilities=local.default_capabilities,
            configuration_digest=config.configuration_digest,
        )
        result = _retry(admin, {"registration": registration.model_dump(mode="json"), "update": update})
        protected_write(registration_path, registration.model_dump_json(indent=2))
        protected_write(runtime_path, config.model_dump_json(indent=2))
        result["runtime_config"] = str(runtime_path)
        updating.close()
        result["service"] = launch("runtime", config, deployment.start)
        if not result.get("targets_initialized"):
            for scope in dict.fromkeys(target.scope_id for target in local.targets):
                _grant(admin, admin_identity, scope)
            _retry(
                admin,
                {
                    "registration": {
                        "action": "initialize_targets",
                        "instance_id": local.id,
                        "targets": [target.model_dump(mode="json") for target in local.targets],
                    }
                },
            )
        return result


def _retry(admin, body):
    deadline = time.monotonic() + 60
    while True:
        try:
            return request(admin, "POST", "/v1/source-management/configure", body)
        except ManagementError as error:
            if (
                error.code
                not in {
                    "management-unavailable",
                    "management-busy",
                    "result-unconfirmed",
                    "instance-unavailable",
                    "capability-unavailable",
                }
                or time.monotonic() >= deadline
            ):
                raise
            time.sleep(0.2)
