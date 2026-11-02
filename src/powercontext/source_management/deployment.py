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

"""Local deployment coordinator; services and collection run in separate processes."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import secrets
import socket
import sys
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

import httpx
import yaml
from pydantic import Field
from typing_extensions import override

from powercontext._logging import log_safely
from powercontext.server.authentication import (
    AuthenticationRejectedError,
    MultiBearerAuthenticationProvider,
)
from powercontext.source_management.config import (
    CollectDeployment,
    Endpoint,
    ManagerConfig,
    RuntimeConfig,
    configuration_digest,
    secret,
)
from powercontext.source_management.models import InstanceRegistration, ManagementError, Model
from powercontext.source_management.service import launch, protected_write

logger = logging.getLogger(__name__)


class InitialTarget(Model):
    scope_id: str
    capability: str
    capability_version: str
    input: dict[str, Any]


class LocalInstance(Model):
    id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$", max_length=128)
    plugin: str
    config: dict[str, Any] = Field(default_factory=dict)
    secrets: dict[str, Path] = Field(default_factory=dict)
    scope_ids: tuple[str, ...] = ()
    targets: tuple[InitialTarget, ...] = ()
    public_principals: tuple[str, ...] = ("*",)
    default_capabilities: tuple[str, ...] = ()
    deployment: CollectDeployment | None = None


class DeploymentAuthentication(MultiBearerAuthenticationProvider):
    """Add generated service credentials without changing user-managed identity files."""

    def __init__(self, path, delegate):
        super().__init__(path)
        self.delegate = delegate

    @override
    async def authenticate(self, request):
        try:
            return await super().authenticate(request)
        except AuthenticationRejectedError:
            if self.delegate is None:
                raise
            return await self.delegate.authenticate(request)

    @override
    def principals(self):
        original = self.delegate.principals() if isinstance(self.delegate, MultiBearerAuthenticationProvider) else ()
        return (*original, *super().principals())

    @override
    async def readiness(self):
        current = await super().readiness()
        if not current.ready or self.delegate is None:
            return current
        return await self.delegate.readiness()


def deployment_root(settings):
    from powercontext.paths import powercontext_data_dir

    address = settings.public_url
    if address is None:
        if settings.http.uds is not None:
            raise ManagementError(
                "server-url-required",
                "UDS Source deployment requires POWERCONTEXT_SERVER_PUBLIC_URL pointing to an HTTP(S) proxy",
            )
        address = f"http://127.0.0.1:{settings.http.port}"
    key = hashlib.sha256((address + "\0" + settings.access.deployment_id).encode()).hexdigest()[:24]
    return (
        (settings.source_management.state_dir or powercontext_data_dir() / "source-deployments" / key)
        .expanduser()
        .resolve()
    )


def credential(root, name, subject):
    """Serialize credential creation and keep their plaintext in protected startup files."""
    from powercontext.source_management.durable import RuntimeOwner

    with_lock = RuntimeOwner(root / "configuration-lock")
    try:
        path = root / (name + ".token")
        if not path.exists():
            protected_write(path, secrets.token_urlsafe(32), immutable=True)
        token = secret(path)
        identities_file = root / "identities.json"
        values = json.loads(identities_file.read_text())["identities"] if identities_file.exists() else []
        digest = hashlib.sha256(token.encode()).hexdigest()
        expected = {"credential_id": name, "token_digest": digest, "subject": {"type": "service", "id": subject}}
        existing = next((v for v in values if v["credential_id"] == name), None)
        if existing is not None and existing != expected:
            raise ManagementError("credential-conflict", "Generated service credential configuration differs")
        if existing is None:
            protected_write(identities_file, json.dumps({"identities": [*values, expected]}))
        return path
    finally:
        with_lock.close()


def prepare(settings, provider):
    if settings.source_plugins_dir is None and not settings.source_management.autostart:
        return provider, None
    if settings.access.mode != "enforced":
        raise ManagementError("access-required", "Automatic Source deployment requires enforced PC Access")
    root = deployment_root(settings)
    key = hashlib.sha256(str(root.resolve()).encode()).hexdigest()[:16]
    subject = "pc-source-deployer-" + key
    manager = "pc-source-manager-" + key
    credential(root, "deployer", subject)
    credential(root, "manager", manager)
    if settings.source_management.manager_subject not in (None, "service:" + manager):
        raise ManagementError("manager-conflict", "Automatic deployment cannot replace a configured remote manager")
    settings.source_management.manager_subject = "service:" + manager
    settings.auth.administrators = (*settings.auth.administrators, "service:" + subject)
    provider = DeploymentAuthentication(root / "identities.json", provider)
    return provider, next(p for p in provider.principals() if p.id == subject)


class DeploymentCoordinator:
    def __init__(self, settings):
        self.settings = settings
        self.root = deployment_root(settings)
        self.server_url = settings.public_url or f"http://127.0.0.1:{settings.http.port}"
        self._lock = asyncio.Lock()

    async def request(self, client, path, body):
        response = await client.post(path, json=body)
        if response.status_code >= 400:
            error = response.json().get("error", {})
            raise ManagementError(
                error.get("code", "deployment-failed"),
                error.get("message", "Deployment request failed"),
                response.status_code,
            )
        return response.json()

    async def manager(self):
        config_path = self.root / "manager.json"
        if config_path.exists():
            config = ManagerConfig.model_validate_json(config_path.read_text())
        else:
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            config = ManagerConfig(
                server=Endpoint(url=self.server_url, credential_file=self.root / "manager.token"),
                deployment_id=self.settings.access.deployment_id,
                manager_id=self.settings.source_management.manager_id,
                state_dir=self.root / "manager",
                port=port,
            )
            protected_write(config_path, config.model_dump_json(indent=2), immutable=True)
        await asyncio.to_thread(launch, "manager", config, self.settings.source_management.process_mode)
        return config

    async def load(self, path: Path, *, update=False):  # noqa: C901 - ordered deployment and readiness checks
        path = await asyncio.to_thread(lambda: path.expanduser().resolve(strict=True))
        local = LocalInstance.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
        if local.deployment is not None:
            raise ManagementError("execution-machine-required", "Run source load on the configured execution machine")
        async with self._lock, AsyncExitStack() as updating:
            manager = await self.manager()
            instance_root = self.root / "instances" / local.id
            execution_subject = "pc-source-" + hashlib.sha256((str(self.root) + local.id).encode()).hexdigest()[:24]
            execution_file = credential(self.root, "execution-" + local.id, execution_subject)
            instance_root.mkdir(parents=True, exist_ok=True, mode=0o700)
            if update:
                from powercontext.source_management.durable import RuntimeOwner

                updating.callback(RuntimeOwner(instance_root).close)
            instance_file = instance_root / "instance.token"
            if not instance_file.exists():
                protected_write(instance_file, secrets.token_urlsafe(32), immutable=True)
            aliases = {k: (path.parent / v).resolve() for k, v in local.secrets.items()}
            config = RuntimeConfig(
                instance_id=local.id,
                deployment_id=self.settings.access.deployment_id,
                plugin=local.plugin,
                plugin_dir=path.parent,
                config=local.config,
                secrets=aliases,
                server=Endpoint(url=self.server_url, credential_file=execution_file),
                management=Endpoint(url=f"http://127.0.0.1:{manager.port}", credential_file=instance_file),
                state_dir=instance_root,
                execution_location=str(path.parent),
                configuration_digest=configuration_digest(local.plugin, local.config, aliases),
            )
            from powercontext.client import PowerContextClient
            from powercontext.http import RegisterSourceDefinitionRequest, SourceDefinitionManifest

            manifests = await inspect_manifests(config)
            token = secret(self.root / "deployer.token")
            async with PowerContextClient(self.server_url, token=token) as client:
                for value in manifests:
                    manifest = SourceDefinitionManifest.model_validate(value)
                    registered = await client.register_source_definition(
                        RegisterSourceDefinitionRequest(manifest=manifest)
                    )
                    if registered != manifest:
                        raise ManagementError("manifest-mismatch", "Server returned a different Definition manifest")
            async with httpx.AsyncClient(
                base_url=self.server_url, headers={"Authorization": "Bearer " + token}, timeout=65
            ) as client:
                registration_path = instance_root / "registration.json"
                previous = json.loads(registration_path.read_text()) if registration_path.exists() else None
                scopes = (
                    tuple(previous["scope_ids"])
                    if previous is not None and not update
                    else tuple(dict.fromkeys((*local.scope_ids, *(t.scope_id for t in local.targets))))
                )
                registration = InstanceRegistration(
                    instance_id=local.id,
                    credential_digest=hashlib.sha256(secret(instance_file).encode()).hexdigest(),
                    execution_location=config.execution_location,
                    scope_ids=scopes,
                    public_principals=local.public_principals,
                    default_capabilities=local.default_capabilities,
                    configuration_digest=config.configuration_digest,
                )
                async with asyncio.timeout(60):
                    while True:
                        try:
                            result = await self.request(
                                client,
                                "/v1/source-management/configure",
                                {"registration": registration.model_dump(mode="json"), "update": update},
                            )
                            break
                        except ManagementError as error:
                            if error.code not in {"management-unavailable", "result-unconfirmed"}:
                                raise
                            await asyncio.sleep(0.2)
                protected_write(registration_path, registration.model_dump_json(indent=2))
                for scope in scopes:
                    await self.request(
                        client,
                        "/v1/access/bindings/create",
                        {
                            "subject": {"type": "service", "id": execution_subject},
                            "resource": {"type": "scope", "scope_id": scope},
                            "role": "scope.contributor",
                            "idempotency_key": "source-"
                            + hashlib.sha256((execution_subject + scope).encode()).hexdigest()[:48],
                        },
                    )
                protected_write(instance_root / "runtime-config.json", config.model_dump_json(indent=2))
                await updating.aclose()
                result["service"] = await asyncio.to_thread(
                    launch, "runtime", config, self.settings.source_management.process_mode
                )
                if result.get("targets_initialized"):
                    return result
                principal_response = await client.get("/v1/access/me")
                principal_response.raise_for_status()
                initializer = principal_response.json()["principal"]
                for scope in dict.fromkeys(t.scope_id for t in local.targets):
                    await self.request(
                        client,
                        "/v1/access/bindings/create",
                        {
                            "subject": {"type": initializer["type"], "id": initializer["id"]},
                            "resource": {"type": "scope", "scope_id": scope},
                            "role": "scope.contributor",
                            "idempotency_key": "source-seed-"
                            + hashlib.sha256((initializer["id"] + scope).encode()).hexdigest()[:48],
                        },
                    )
                async with asyncio.timeout(60):
                    while True:
                        try:
                            await self.request(
                                client,
                                "/v1/source-management/configure",
                                {
                                    "registration": {
                                        "action": "initialize_targets",
                                        "instance_id": local.id,
                                        "targets": [t.model_dump(mode="json") for t in local.targets],
                                    }
                                },
                            )
                            break
                        except ManagementError as error:
                            if error.code not in {
                                "instance-unavailable",
                                "management-unavailable",
                                "capability-unavailable",
                                "result-unconfirmed",
                            }:
                                raise
                            await asyncio.sleep(0.2)
                return result

    async def startup(self):
        # Run after the HTTP listener becomes ready; one failed plugin must not block built-ins.
        async with httpx.AsyncClient(timeout=2) as client:
            for _ in range(100):
                try:
                    response = await client.get(self.server_url + "/health")
                    if response.status_code < 500:
                        break
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.1)
        if self.settings.source_management.autostart:
            try:
                await self.manager()
            except Exception as error:
                log_safely(
                    logger,
                    logging.ERROR,
                    "Source manager startup failed; inspect local service configuration",
                    extra={
                        "error_code": error.code
                        if isinstance(error, ManagementError)
                        and error.code in {"background-unsupported", "service-unavailable"}
                        else "deployment-failed",
                    },
                )
        directory = self.settings.source_plugins_dir
        if directory is not None:
            for path in sorted(directory.expanduser().glob("*/powercontext.yml")):
                try:
                    await self.load(path)
                except Exception as error:
                    log_safely(
                        logger,
                        logging.ERROR,
                        "Source instance preparation failed; inspect local configuration",
                        extra={
                            "error_code": error.code
                            if isinstance(error, ManagementError)
                            and error.code in {"background-unsupported", "service-unavailable"}
                            else "deployment-failed",
                        },
                    )


async def inspect_manifests(config):
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "powercontext.source_management.inspection",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        async with asyncio.timeout(30):
            output, _ = await process.communicate(config.model_dump_json().encode())
        if process.returncode or len(output) > 2 * 1024 * 1024:
            raise ManagementError("plugin-inspection-failed", "The plugin entry or declarations could not be loaded")
        return json.loads(output)
    except (ValueError, TimeoutError) as error:
        raise ManagementError(
            "plugin-inspection-failed", "The plugin inspection did not return valid declarations"
        ) from error
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
