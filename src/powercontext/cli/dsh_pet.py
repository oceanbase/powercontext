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


"""Opt in to dsh-pet memory in one DSH profile."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import typer

from powercontext.cli.authorization import credential_path, read_stored_authorization
from powercontext.cli.dsh_runtime import DshTarget
from powercontext.cli.dsh_transport import read_dsh_settings
from powercontext.cli.installation import SetupError, write_bytes, write_json
from powercontext.cli.transport import is_remote_http
from powercontext.client import PowerContextClient, UnauthorizedResponseError
from powercontext.http import AccessCheckRequest

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from powercontext.cli.transport import SetupTransport
    from powercontext.http import ConnectionIdentity, ScopePage

ADAPTER = "powercontext-dsh-pet"
PET_VERSION = "0.4.8"
DSH_VERSION = "0.2.2-rc.1"
MAX_BINDINGS = 16
STATUS_AGE_SECONDS = 10


def parse_pet_bindings(values: list[str]) -> dict[str, str]:
    """Require exact, independent Scope selections."""
    bindings = {}
    for value in values:
        pet, separator, scope = value.partition("=")
        if (
            not separator
            or not pet.strip()
            or not scope.strip()
            or len(pet) > 256
            or len(scope) > 256
            or pet in bindings
            or scope in bindings.values()
        ):
            raise ValueError("Use unique PET_ID=SCOPE_ID pairs with a different existing Scope for each pet.")  # noqa: TRY003
        bindings[pet] = scope
    if not 1 <= len(bindings) <= MAX_BINDINGS:
        raise ValueError(f"Provide between one and {MAX_BINDINGS} pet bindings.")  # noqa: TRY003
    return bindings


def _root(target: DshTarget) -> Path:
    return target.directory / f".{ADAPTER}"


def _object(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("version") != 1:
        raise ValueError("Invalid pet memory settings or status.")  # noqa: TRY003
    return data


def _installed(target: DshTarget) -> tuple[dict[str, Any], dict[str, Any]]:
    packages = read_dsh_settings(profile=target.profile, executable=target.command, inspect_pet=True).get(
        "petPackages", {}
    )
    return packages.get("dsh-pet", {}), packages.get(ADAPTER, {})


def _ensure_private_directory(root: Path) -> None:
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name != "nt" and root.stat().st_mode & 0o077:
        raise ValueError("The pet memory state directory must be private to the current user.")  # noqa: TRY003


def disable_pet_memory(target: DshTarget, *, discard: bool = False) -> dict[str, Any]:
    """Stop delivery without touching the DSH installation or Server."""
    root = _root(target)
    path = root / "bindings.json"
    if path.exists():
        try:
            settings = _object(path)
        except (ValueError, UnicodeError):
            # The runtime already rejects malformed settings. Remove the configuration
            # when explicitly disabling so the next opt-in starts from a clean binding.
            path.unlink()
        else:
            settings["enabled"] = False
            if discard:
                for item in settings["bindings"]:
                    item["generation"] = uuid4().hex
            write_json(path, settings)
    if discard and (queue := root / "queue").exists():
        # The running provider observes the disabled settings before new sends. Moving
        # the directory prevents old work from reappearing in a future binding.
        retired = root / (".discard-" + uuid4().hex)
        queue.rename(retired)
        shutil.rmtree(retired)
    return {"status": "disabled", "discarded_pending": discard}


@asynccontextmanager
async def _verified_destination(server_url: str) -> AsyncIterator[tuple[PowerContextClient, ConnectionIdentity]]:
    async with httpx.AsyncClient(timeout=4, trust_env=False) as transport:
        # Match runtime: auth-disabled bindings are loopback-only and must work
        # without sending a credential owned by the ordinary DSH integration.
        if urlsplit(server_url).hostname in {"localhost", "127.0.0.1", "::1"}:
            async with PowerContextClient(server_url, http_client=transport, allow_insecure_http=False) as client:
                try:
                    identity = await client.get_connection_identity()
                except UnauthorizedResponseError:
                    pass  # This destination may require its URL-bound DSH credential.
                else:
                    if (
                        identity.authentication.value != "disabled"
                        or identity.access_mode.value != "disabled"
                        or identity.principal is not None
                    ):
                        raise ValueError("Unexpected identity for anonymous pet memory.")  # noqa: TRY003
                    yield client, identity
                    return

        authorization = read_stored_authorization(credential_path("dsh"), server_url=server_url).authorization
        if not authorization:
            raise ValueError("An authenticated pet memory Server requires a URL-bound DSH credential.")  # noqa: TRY003
        token = authorization.removeprefix("Bearer ")
        async with PowerContextClient(
            server_url, token=token, http_client=transport, allow_insecure_http=False
        ) as client:
            identity = await client.get_connection_identity()
            if identity.authentication.value != "authenticated" or identity.principal is None:
                raise ValueError("The pet memory Server did not verify an authenticated principal.")  # noqa: TRY003
            yield client, identity


async def _list_pet_scopes(server_url: str) -> ScopePage:
    async with _verified_destination(server_url) as (client, _):
        return await client.list_scopes(limit=20)


def _prompt_pet_scope(server_url: str) -> str:
    try:
        page = asyncio.run(_list_pet_scopes(server_url))
    except Exception:
        # Keep Server error bodies out of the prompt. Some credentials can use an exact Scope but cannot list Scopes.
        typer.echo("Could not list Scopes on this Server. You can enter an existing Scope ID.")
    else:
        if page.items:
            typer.echo("Choose an existing Scope dedicated to this pet:")
            choices = {}
            for index, scope in enumerate(page.items, start=1):
                typer.echo(f"  {index}. {scope.title!r} (ID: {scope.scope_id!r})")
                choices[str(index)] = scope.scope_id
            while True:
                choice = typer.prompt(
                    "Scope number (0 to enter an exact ID, Enter to skip)", default="", show_default=False
                ).strip()
                if choice == "0":
                    break
                if not choice:
                    return ""
                if choice in choices:
                    return choices[choice]
                typer.echo("Choose a number from the list or enter 0.")
        else:
            typer.echo("No Scopes were listed. Create a dedicated Scope on this Server if you do not have one.")

    return typer.prompt("Exact Scope ID (Enter to skip)", default="", show_default=False)


def _prompt_pet_binding(target: DshTarget, server_url: str) -> dict[str, str] | None:
    if not typer.confirm(
        f"Enable dsh-pet long-term memory in the {target.profile} profile? "
        f"New text questions and replies will be sent to {server_url}, "
        "which may process them with its configured model providers.",
        default=False,
    ):
        return None

    pet = typer.prompt("Pet instance ID", default="main").strip()
    if "=" in pet:
        raise ValueError("Pet instance ID cannot contain '='.")  # noqa: TRY003
    scope_id = _prompt_pet_scope(server_url)
    return parse_pet_bindings([f"{pet}={scope_id}"]) if scope_id else None


async def _verify_pet_scopes(server_url: str, bindings: dict[str, str]) -> dict[str, Any]:
    async with _verified_destination(server_url) as (client, identity):
        scopes = list(bindings.values())
        async with asyncio.TaskGroup() as tasks:
            for scope in scopes:
                tasks.create_task(client.get_scope(scope))
        if identity.access_mode.value == "enforced":
            check = AccessCheckRequest.model_validate({
                "match": "all",
                "requirements": [
                    {"action": action, "resource": {"type": "scope", "scope_id": scope}}
                    for scope in scopes
                    for action in ("scope.read", "scope.contribute")
                ],
            })
            if not (await client.check_access(check)).allowed:
                raise ValueError("Each pet Scope requires read and contribution access.")  # noqa: TRY003
    data = identity.model_dump(mode="json")
    if data["principal"] is not None:
        data["principal"].pop("description", None)
    return data


def enable_pet_memory(  # noqa: C901 - perform and report each setup boundary separately.
    target: DshTarget, main_package: Path, connection: SetupTransport, bindings: dict[str, str] | None
) -> dict[str, Any]:
    """Enable the adapter with explicit bindings, or prompt for a first binding."""
    from powercontext.cli.dsh import _run_dsh, dsh_executable

    def unchanged(reason: str) -> dict[str, str]:
        return {"status": "unchanged", "reason": reason}

    try:
        pet, _ = _installed(target)
        if pet.get("entry") != "enabled":
            return unchanged("dsh_pet_" + pet.get("entry", "missing"))
        executable = target.command or dsh_executable()
        if pet.get("version") != PET_VERSION or _run_dsh("--version", executable=executable).strip() != DSH_VERSION:
            return unchanged("dsh_pet_or_host_version_unqualified")
        if is_remote_http(connection.server_url):
            return unchanged("remote_https_required")
        package = main_package.parent / "dsh-pet"
        if not (package / "lib/index.js").is_file() or not (package / "package.json").is_file():
            return unchanged("adapter_package_missing")
        root = _root(target)
        path = root / "bindings.json"
        if bindings is None and path.exists():
            return unchanged("pet_memory_already_configured")
        previous = _object(path) if path.exists() else {}
    except (OSError, ValueError, SetupError):
        return unchanged("cannot_inspect_pet_profile")

    if bindings is None:
        try:
            bindings = _prompt_pet_binding(target, connection.server_url)
        except typer.Abort:
            return unchanged("opt_in_cancelled")
        except ValueError:
            return unchanged("invalid_pet_or_scope_id")
        if bindings is None:
            return unchanged("opt_in_skipped")

    try:
        identity = asyncio.run(_verify_pet_scopes(connection.server_url, bindings))
    except Exception:
        # Never surface transport, provider, or server error bodies in setup output.
        return unchanged("server_identity_scope_or_permissions_unverified")

    try:
        _ensure_private_directory(root)
        patch = target.directory / "cordis.patch.yml"
        original = patch.read_bytes() if patch.exists() else None
        try:
            _run_dsh("plugin", "--profile", target.profile, "add", str(package), executable=executable)
        finally:
            if original is not None and patch.read_bytes() != original:
                write_bytes(patch, original)
        pet, adapter = _installed(target)
        if pet.get("entry") != "enabled" or adapter.get("entry") != "enabled" or not adapter.get("version"):
            return unchanged("adapter_registration_not_verified")
        same_destination = previous.get("server_url") == connection.server_url and previous.get("identity") == identity
        saved = (
            {(row["pet_id"], row["scope_id"]): row["generation"] for row in previous.get("bindings", [])}
            if same_destination
            else {}
        )
        write_json(
            path,
            {
                "version": 1,
                "enabled": True,
                "server_url": connection.server_url,
                "identity": identity,
                "bindings": [
                    {"pet_id": pet, "scope_id": scope, "generation": saved.get((pet, scope)) or uuid4().hex}
                    for pet, scope in bindings.items()
                ],
            },
        )
    except (OSError, ValueError, SetupError, KeyError, TypeError):
        return unchanged("adapter_installation_or_configuration_failed")
    return {
        "status": "enabled",
        "server_url": connection.server_url,
        "bindings": bindings,
        "runtime": "restart_dsh_to_load_adapter",
        "data": "Completed text exchanges go to this Server; it may process them with its configured model providers.",
    }


def _binding_observation(item: dict[str, Any]) -> dict[str, Any]:
    observed = {"scope_id": item["scope_id"]}
    pending = item.get("pending")
    if type(pending) is int and 0 <= pending <= 128:
        observed["pending"] = pending
    outcomes = {
        "queued",
        "accepted",
        "ready",
        "empty",
        "timeout",
        "unavailable",
        "identity_changed",
        "permission_denied",
        "conflict",
        "rejected",
        "invalid_response",
        "queue_unavailable",
    }
    for stage in ("capture", "recall"):
        value = item.get(stage)
        if isinstance(value, str) and value in outcomes:
            observed[stage] = value
    return observed


def pet_memory_diagnostic(target: DshTarget):
    """Separate installed and configured status from observations made by a running host."""
    from powercontext.cli.system import Diagnostic, DiagnosticStatus

    try:
        pet, adapter = _installed(target)
    except (OSError, ValueError, SetupError):
        return Diagnostic(DiagnosticStatus.SKIPPED, "Cannot inspect the optional pet memory entries.")
    checks = {
        "pet_entry": pet.get("entry", "missing"),
        "pet_version": pet.get("version") or "not_installed",
        "adapter_entry": adapter.get("entry", "missing"),
        "adapter_version": adapter.get("version") or "not_installed",
        "runtime": "not_observed",
    }
    root = _root(target)
    try:
        config = _object(root / "bindings.json")
    except FileNotFoundError:
        return Diagnostic(DiagnosticStatus.SKIPPED, "Pet memory is off.", checks)
    except (OSError, ValueError):
        return Diagnostic(DiagnosticStatus.DEGRADED, "Pet memory configuration cannot be read.", checks)
    if config.get("enabled") is not True:
        return Diagnostic(DiagnosticStatus.SKIPPED, "Pet memory is off; pending records are not sent.", checks)
    try:
        runtime = _object(root / "status.json")
        timestamp = float(runtime.get("observed_at", 0)) / 1000
        if 0 <= time.time() - timestamp < STATUS_AGE_SECONDS:
            observed = runtime["bindings"]
            expected = config["bindings"]
            provider = runtime["provider"]
            if provider == "provider_conflict":
                checks["runtime"] = provider
            if provider == "registered" and [(item["generation"], item["scope_id"]) for item in observed] == [
                (item["generation"], item["scope_id"]) for item in expected
            ]:
                checks["runtime"] = provider
                checks["bindings"] = json.dumps(
                    [_binding_observation(item) for item in observed], ensure_ascii=False, separators=(",", ":")
                )
    except (OSError, ValueError, KeyError, TypeError):
        pass
    checks["server_url"] = str(config.get("server_url", ""))
    if checks["runtime"] == "registered" and checks["adapter_entry"] == "enabled" and checks["pet_entry"] == "enabled":
        return Diagnostic(
            DiagnosticStatus.OK, "Pet memory provider observed; Source acceptance is not extraction.", checks
        )
    return Diagnostic(
        DiagnosticStatus.DEGRADED, "Pet memory configured; check the running DSH host and provider slot.", checks
    )
