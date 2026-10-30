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

# ruff: noqa: TRY003

"""Install the native MiniMax package and inspect its effective connection."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import tempfile
from contextlib import closing
from pathlib import Path, PurePosixPath
from typing import Any, cast

from powercontext.cli.authorization import normalize_authorization, setup_authorization_value
from powercontext.cli.installation import SetupError, publish
from powercontext.client.transport_policy import load_client_settings, normalize_client_url
from powercontext.paths import powercontext_data_dir

_MANIFEST = ".minimax-plugin/plugin.json"
_SKILL = "skills/powercontext-project-context/SKILL.md"
_MCP = "powercontext.mcp.json"


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()


def _object(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        raise SetupError("MiniMax configuration must not be a symbolic link")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeError, ValueError):
        raise SetupError("Cannot read MiniMax JSON configuration; repair it before setup") from None
    if not isinstance(value, dict):
        raise SetupError("MiniMax configuration must contain a JSON object")
    return value


def _run(command: str, arguments: list[str], data_dir: Path | None = None, *, home: Path | None = None) -> object:
    environment = dict(os.environ)
    if data_dir is not None:
        environment["MINIMAX_DATA_DIR"] = str(data_dir)
    if home is not None:
        environment.update(HOME=str(home), USERPROFILE=str(home))
    try:
        result = subprocess.run(  # noqa: S603 - the user selects the native launcher, without a shell.
            [command, "plugin", *arguments, "--json"],
            env=environment,
            cwd=home,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError, UnicodeError):
        raise SetupError("MiniMax CLI could not run or timed out; check --minimax-command") from None
    if result.returncode:
        raise SetupError("MiniMax CLI failed; inspect the selected installation with mcode plugin --help")
    try:
        return json.loads(result.stdout)
    except ValueError:
        raise SetupError("MiniMax CLI returned invalid JSON; use a supported CLI build") from None


def _command(explicit: Path | None, saved: dict[str, Any], data_dir: Path | None) -> str | None:
    configured = explicit or (saved.get("minimax_command") if str(data_dir) == saved.get("minimax_data_dir") else None)
    if configured:
        candidate = Path(configured).expanduser().absolute()
        if not candidate.is_file() or not os.access(candidate, os.X_OK):
            raise SetupError("MiniMax launcher is unavailable; correct --minimax-command")
        return str(candidate)
    return shutil.which("mcode")


def _data_dir(explicit: Path | None, saved: dict[str, Any], command: str | None, *, read_only: bool) -> Path:
    override = (
        explicit or os.environ.get("MINIMAX_DATA_DIR", "").strip() or os.environ.get("MAVIS_DATA_DIR", "").strip()
    )
    if override:
        return Path(override).expanduser().resolve()
    if saved.get("minimax_data_dir"):
        return Path(saved["minimax_data_dir"]).expanduser().resolve()
    if command:
        if read_only:
            raise SetupError("Specify --minimax-data-dir for read-only doctor before the first setup")
        marketplaces = _run(command, ["marketplace", "list"])
        if isinstance(marketplaces, list):
            local = [entry for entry in marketplaces if isinstance(entry, dict) and entry.get("name") == "local"]
            path = local[0].get("path") if len(local) == 1 else None
            if isinstance(path, str) and path:
                return Path(path).expanduser().resolve().parent
        raise SetupError("Cannot determine MiniMax data directory; specify --minimax-data-dir")
    if os.environ.get("MAVIS_PROFILE", "").strip():
        raise SetupError("MiniMax profile requires --minimax-data-dir when mcode is unavailable")
    return Path.home() / ".minimax"


def _relative(value: object) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise SetupError("MiniMax package contains an invalid resource path")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or path.as_posix() != value or ":" in value:
        raise SetupError("MiniMax package resource must stay inside its package")
    return value


def _files(package: Path) -> dict[str, str]:
    files = {}
    for path in package.rglob("*"):
        if path.is_symlink():
            raise SetupError("MiniMax package contains a symbolic link; resolve it before installation")
        if path.is_file():
            files[path.relative_to(package).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return files


def require_minimax_package(package: Path) -> dict[str, str]:
    """Validate the native manifest and its complete, portable resources before writes."""
    manifest = _object(package / _MANIFEST)
    if manifest.get("name") != "powercontext" or manifest.get("schemaVersion") != 1:
        raise SetupError("Expected a schemaVersion 1 native powercontext MiniMax manifest")
    resources = [_MANIFEST, "LICENSE", manifest.get("icon")]
    for field, required in (("skills", _SKILL), ("mcpServers", _MCP)):
        references = manifest.get(field)
        if not isinstance(references, list) or required not in references:
            raise SetupError("MiniMax manifest omits the required Skill or MCP configuration")
        resources.extend(references)
    for resource in resources:
        relative = _relative(resource)
        if not (package / relative).is_file():
            raise SetupError(f"MiniMax package is incomplete: missing {relative}")
    config = _object(package / _MCP)
    if config.get("schemaVersion") != 1:
        raise SetupError("Unsupported MiniMax MCP configuration schema")
    _mcp_entry(config, required=True)
    guidance = _object(package / "skills/powercontext-project-context/.powercontext-guidance.json")
    if guidance.get("schema_version") != 1 or not isinstance(guidance.get("resources"), list):
        raise SetupError("MiniMax Skill resource inventory is missing")
    for resource in guidance["resources"]:
        path = package / "skills/powercontext-project-context" / _relative(resource)
        if not path.is_file():
            raise SetupError("MiniMax Skill reference is missing")
    return _files(package)


def _mcp_entry(config: dict[str, Any], *, required: bool = False) -> dict[str, Any] | None:
    servers = config.get("mcpServers", {})
    if not isinstance(servers, dict):
        raise SetupError("MiniMax mcpServers must be a JSON object")
    entry = servers.get("powercontext")
    if entry is None and not required:
        return None
    if not isinstance(entry, dict) or entry.get("type") != "streamable-http":
        raise SetupError("MiniMax powercontext MCP entry must use streamable-http")
    if not isinstance(entry.get("enabled", True), bool):
        raise SetupError("MiniMax MCP enabled must be a boolean")
    _endpoint(entry)
    headers = entry.get("headers", {})
    if not isinstance(headers, dict) or any(
        not isinstance(k, str) or not isinstance(v, str) for k, v in headers.items()
    ):
        raise SetupError("MiniMax MCP headers must be a string-valued object")
    if any("${" in value for value in headers.values()):
        raise SetupError("MiniMax private MCP headers must contain literal values")
    authorization = [value for key, value in headers.items() if key.lower() == "authorization"]
    if len(authorization) > 1:
        raise SetupError("MiniMax MCP has duplicate Authorization headers")
    if authorization:
        normalize_authorization(authorization[0])
    return entry


def _endpoint(entry: dict[str, Any]) -> str:
    value = entry.get("url")
    if not isinstance(value, str) or "${" in value:
        raise SetupError("MiniMax MCP URL must be a literal HTTP(S) endpoint")
    endpoint = normalize_client_url(value)
    if not endpoint.endswith("/mcp"):
        raise SetupError("MiniMax powercontext MCP URL must end in /mcp")
    return endpoint.removesuffix("/mcp")


def _verify_discovery(result: object, package: Path) -> None:
    if not isinstance(result, dict) or not isinstance(result.get("installed"), list):
        raise SetupError("MiniMax plugin list returned an unsupported schema")
    result = cast(dict[str, Any], result)
    matches = [
        entry
        for entry in result["installed"]
        if isinstance(entry, dict) and entry.get("name") == "powercontext" and entry.get("marketplaceName") == "local"
    ]
    if len(matches) != 1 or matches[0].get("installed") is not True:
        raise SetupError(
            "MiniMax omitted the installed local powercontext plugin; check its manifest and selected directory"
        )
    plugin = matches[0]
    if plugin.get("enabled") is not True:
        raise SetupError(
            "MiniMax powercontext plugin is disabled; enable powercontext@local in the selected MiniMax installation"
        )
    manifest = _object(package / _MANIFEST)
    if plugin.get("version") != manifest.get("version"):
        raise SetupError("MiniMax discovered a different plugin version; refresh or restart the selected host")
    counts = plugin.get("capabilities", {})
    if not isinstance(counts, dict) or any(
        type(counts.get(key)) is not int or counts[key] < len(manifest[field])
        for key, field in (("skillCount", "skills"), ("mcpServerCount", "mcpServers"))
    ):
        raise SetupError("MiniMax did not discover the required Skill and MCP resources")


class MiniMaxInstaller:
    def __init__(
        self, *, minimax_data_dir: Path | None = None, minimax_command: Path | None = None, read_only: bool = False
    ) -> None:
        saved = load_client_settings("minimax")
        override = (
            minimax_data_dir
            or os.environ.get("MINIMAX_DATA_DIR", "").strip()
            or os.environ.get("MAVIS_DATA_DIR", "").strip()
        )
        selected = (
            Path(override).expanduser().resolve()
            if override
            else Path(saved["minimax_data_dir"])
            if saved.get("minimax_data_dir")
            else None
        )
        self.command = _command(minimax_command, saved, selected)
        self.data_dir = _data_dir(minimax_data_dir, saved, self.command, read_only=read_only)
        self.plugin = self.data_dir / "plugins/powercontext"
        identity = hashlib.sha256(str(self.data_dir).encode()).hexdigest()
        self.record = powercontext_data_dir() / "installations/minimax" / f"{identity}.json"

    def native_entry(self) -> dict[str, Any] | None:
        """Read the private connection, or the bundle when the private entry is disabled."""
        entry = _mcp_entry(_object(self.data_dir / "mcp.json"))
        if entry is not None and entry.get("enabled", True):
            return entry
        return _mcp_entry(_object(self.plugin / _MCP))

    def native_endpoint(self) -> str | None:
        entry = self.native_entry()
        return _endpoint(entry) if entry is not None else None

    def install(self, source, *, connection=None) -> dict[str, Any]:  # noqa: C901
        self.native_entry()  # Malformed private configuration must fail before publication.
        if self.plugin.is_symlink() or self.plugin.parent.is_symlink():
            raise SetupError("MiniMax plugin installation must not replace a symbolic link")
        with source.package("minimax") as package:
            incoming = require_minimax_package(package)
            bundled = _object(package / _MCP)
            if connection is not None:
                _, _, selected_bundle, _ = self._connection_entries(connection, bundled)
                bundled["mcpServers"]["powercontext"] = selected_bundle
            previous = _object(self.record)
            if previous and (previous.get("version") != 1 or previous.get("data_dir") != str(self.data_dir)):
                raise SetupError("Unsupported MiniMax installation record; restore it before setup")
            owned = previous.get("files", {})
            if not isinstance(owned, dict):
                raise SetupError("Invalid MiniMax managed file inventory")
            owned = {_relative(name): digest for name, digest in owned.items()}
            current = _files(self.plugin) if self.plugin.is_dir() else {}
            if self.plugin.exists() and not self.plugin.is_dir():
                raise SetupError("MiniMax plugin destination must be a directory")
            if current and not previous:
                if any(current.get(name) != digest for name, digest in incoming.items()):
                    raise SetupError(
                        "Unmanaged MiniMax installation differs; back it up outside plugins and migrate before setup"
                    )
                owned = incoming
            for name in owned.keys() | incoming.keys():
                if (self.plugin / name).is_dir():
                    raise SetupError(f"MiniMax installation conflict: {name} is a directory")
                if any(parent.is_file() for parent in (self.plugin / name).parents if parent != self.plugin):
                    raise SetupError(f"MiniMax installation conflict: a parent of {name} is a file")
                digest = current.get(name)
                if digest is None and (self.plugin / name).is_file():
                    raise SetupError(f"MiniMax installation conflict: {name} aliases an existing file")
                if digest is not None and digest != owned.get(name) and digest != incoming.get(name):
                    raise SetupError(f"MiniMax installation conflict: {name}; preserve your changes before setup")
            record = {
                "version": 1,
                "data_dir": str(self.data_dir),
                "files": incoming,
                "source": source.location,
                "ref": source.ref,
            }
            self.plugin.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix=".powercontext-", dir=self.plugin.parent) as temporary:
                staged = Path(temporary) / "powercontext"
                if self.plugin.exists():
                    shutil.copytree(self.plugin, staged)
                else:
                    staged.mkdir()
                for name in owned.keys() - incoming.keys():
                    (staged / name).unlink(missing_ok=True)
                shutil.copytree(package, staged, dirs_exist_ok=True)
                if connection is not None:
                    content = _json_bytes(bundled)
                    (staged / _MCP).write_bytes(content)
                    incoming[_MCP] = hashlib.sha256(content).hexdigest()
                    if os.name != "nt":
                        (staged / _MCP).chmod(0o600)
                # Check the final projection rather than accepting copytree's directory semantics.
                published = require_minimax_package(staged)
                if any(published.get(name) != digest for name, digest in incoming.items()):
                    raise SetupError("MiniMax staged package differs from its source; check filesystem path collisions")
                record["files"] = incoming
                publish(replacements=[(staged, self.plugin)], writes={self.record: _json_bytes(record)})
        return {"plugin_path": str(self.plugin), "data_dir": str(self.data_dir), "files": "installed"}

    def _bundle_with_saved_connection(self, bundled):
        """Apply installed connection choices to a fresh distribution's MCP declaration."""
        bundle_entry = _mcp_entry(bundled)
        installed_bundle = _mcp_entry(_object(self.plugin / _MCP))
        if bundle_entry is not None and installed_bundle is not None:
            bundle_entry = dict(bundle_entry)
            bundle_entry.pop("headers", None)
            bundle_entry.update({key: installed_bundle[key] for key in ("url", "headers") if key in installed_bundle})
        return bundle_entry

    def _connection_entries(self, connection, bundled):
        config = _object(self.data_dir / "mcp.json")
        existing = _mcp_entry(config)
        bundle_entry = self._bundle_with_saved_connection(bundled)
        preserve_disabled_endpoint = (
            existing is not None and existing.get("enabled") is False and not connection.endpoint_explicit
        )
        endpoint = _endpoint(existing) if preserve_disabled_endpoint else connection.server_url
        target_url = existing["url"] if preserve_disabled_endpoint else endpoint + "/mcp"
        entry: dict[str, Any] = dict(existing or bundle_entry or {"type": "streamable-http"})
        headers = dict(entry.get("headers", {}))
        authorization = setup_authorization_value("minimax")
        state = "preserved" if any(key.lower() == "authorization" for key in headers) else "not_configured"
        credential_source = existing or bundle_entry
        if authorization and authorization.strip():
            headers = {key: value for key, value in headers.items() if key.lower() != "authorization"}
            headers["Authorization"] = normalize_authorization(authorization)
            state = "configured"
        elif credential_source is not None and _endpoint(credential_source) != endpoint and state == "preserved":
            raise SetupError(
                "MiniMax endpoint changed; provide a new authorization explicitly before moving credentials"
            )
        entry.update(type="streamable-http", url=target_url)
        if headers:
            entry["headers"] = headers
        if bundle_entry is not None and not preserve_disabled_endpoint:
            bundle_entry = dict(bundle_entry)
            if entry.get("enabled") is not False:
                bundle_entry.update(url=target_url)
                if headers:
                    bundle_entry["headers"] = headers
                else:
                    bundle_entry.pop("headers", None)
            else:
                bundle_headers = dict(bundle_entry.get("headers", {}))
                if authorization and authorization.strip():
                    bundle_headers = {k: v for k, v in bundle_headers.items() if k.lower() != "authorization"}
                    bundle_headers["Authorization"] = normalize_authorization(authorization)
                elif _endpoint(bundle_entry) != endpoint and any(
                    key.lower() == "authorization" for key in bundle_headers
                ):
                    raise SetupError("MiniMax endpoint changed; provide a new authorization explicitly")
                bundle_entry.update(url=endpoint + "/mcp")
                if bundle_headers:
                    bundle_entry["headers"] = bundle_headers
        return config, entry, bundle_entry, state

    def configure(self, connection) -> dict[str, str]:
        from powercontext.cli.transport import setup_transport_updates

        path = self.data_dir / "mcp.json"
        bundled = _object(self.plugin / _MCP)
        config, entry, bundle_entry, state = self._connection_entries(connection, bundled)
        existing = _mcp_entry(config)
        updates = setup_transport_updates(connection)
        for key, content in list(updates.items()):
            saved = json.loads(content)
            saved["hosts"]["minimax"].update(minimax_data_dir=str(self.data_dir), minimax_command=self.command)
            updates[key] = _json_bytes(saved)
        modes = {}
        # Leave unchanged disabled overrides untouched; active entries retain the
        # existing owner-only permission handling during setup.
        if existing != entry or entry.get("enabled") is not False:
            config.setdefault("mcpServers", {})["powercontext"] = entry
            updates[path] = _json_bytes(config)
            if os.name != "nt":
                modes[path] = stat.S_IMODE(path.stat().st_mode) & 0o600 if path.exists() else 0o600
        if bundle_entry is not None and bundle_entry != _mcp_entry(bundled):
            bundled["mcpServers"]["powercontext"] = bundle_entry
            content = _json_bytes(bundled)
            updates[self.plugin / _MCP] = content
            previous = _object(self.record)
            if previous:
                owned = previous.get("files", {})
                current = hashlib.sha256((self.plugin / _MCP).read_bytes()).hexdigest()
                if (
                    previous.get("version") != 1
                    or previous.get("data_dir") != str(self.data_dir)
                    or not isinstance(owned, dict)
                    or owned.get(_MCP) != current
                ):
                    raise SetupError(
                        "MiniMax installation conflict: preserve the modified MCP package before configuration"
                    )
                previous["files"][_MCP] = hashlib.sha256(content).hexdigest()
                updates[self.record] = _json_bytes(previous)
            if os.name != "nt":
                modes[self.plugin / _MCP] = 0o600

        def secure_files():
            for target, mode in modes.items():
                target.chmod(mode)

        publish(writes=updates, action=secure_files)
        return {
            "authorization_state": state,
            "private_mcp_state": "disabled" if entry.get("enabled") is False else "enabled",
        }

    def _native_snapshot(self):
        """Run the native loader against a disposable copy of its local plugin state."""
        assert self.command is not None  # noqa: S101 - verify handles an unavailable launcher.
        with tempfile.TemporaryDirectory(prefix="powercontext-minimax-doctor-") as temporary:
            home = Path(temporary)
            data = home / "data"
            plugin = data / "plugins/powercontext"
            shutil.copytree(self.plugin, plugin)
            database = self.data_dir / "v2/sqlite/runtime-state.sqlite"
            if database.is_file():
                snapshot = home / "native-state.sqlite"
                # Copy WAL too: opening the real database, even read-only, can create/update SHM.
                for suffix in ("", "-wal"):
                    source = Path(str(database) + suffix)
                    if source.is_file():
                        shutil.copyfile(source, Path(str(snapshot) + suffix))
                with closing(sqlite3.connect(snapshot)) as database_copy:
                    table = database_copy.execute(
                        "SELECT name FROM sqlite_master WHERE name = 'local_runtime_plugin_local_disabled'"
                    ).fetchone()
                    if table is None:
                        raise SetupError("Unsupported MiniMax native state; use a qualified CLI build")
                    disabled = database_copy.execute(
                        "SELECT 1 FROM local_runtime_plugin_local_disabled WHERE canonical_root = ?",
                        (str(self.plugin.resolve()),),
                    ).fetchone()
                if disabled:
                    # Reproduce only enablement through the native API, never real sessions/providers.
                    _run(self.command, ["disable", "powercontext@local"], data, home=home)
            return _run(self.command, ["list", "--marketplace", "local"], data, home=home)

    def _verify_connections(self):
        private = _mcp_entry(_object(self.data_dir / "mcp.json"))
        bundle = _mcp_entry(_object(self.plugin / _MCP), required=True)
        if (
            private is not None
            and private.get("enabled", True)
            and (
                bundle is None
                or _endpoint(private) != _endpoint(bundle)
                or private.get("headers", {}) != bundle.get("headers", {})
            )
        ):
            raise SetupError("MiniMax private and plugin MCP connections differ; rerun setup or config agent minimax")

    def verify(self):
        from powercontext.cli.system import Diagnostic, DiagnosticStatus

        checks = {}
        try:
            require_minimax_package(self.plugin)
        except (OSError, ValueError, SetupError) as error:
            checks["package"] = Diagnostic(DiagnosticStatus.FAILED, str(error))
            checks["native"] = Diagnostic(DiagnosticStatus.SKIPPED, "Native check skipped: package is invalid")
            return checks
        else:
            checks["package"] = Diagnostic(
                DiagnosticStatus.OK, "Native manifest and all packaged resources are present"
            )
        if self.command is None:
            checks["native"] = Diagnostic(
                DiagnosticStatus.DEGRADED,
                "Files only: mcode is unavailable; install the CLI or set --minimax-command, then rerun doctor",
            )
            return checks
        try:
            result = self._native_snapshot()
            _verify_discovery(result, self.plugin)
            self._verify_connections()
            checks["native"] = Diagnostic(
                DiagnosticStatus.OK,
                "Native loader verified a read-only snapshot of the local plugin and enablement state; "
                "host reports the required Skill/MCP counts "
                "(not a connection test). Keep Server running; refresh /plugins or restart MiniMax using the selected data directory.",
            )
        except (OSError, ValueError, SetupError, sqlite3.Error) as error:
            checks["native"] = Diagnostic(DiagnosticStatus.FAILED, str(error))
        return checks

    def server_checks(self):
        from powercontext.cli.system import Diagnostic, DiagnosticStatus, server_diagnostics
        from powercontext.client import PowerContextClient
        from powercontext.client.errors import ServerResponseError

        entry = self.native_entry()
        if entry is None:
            raise SetupError("MiniMax has no effective PowerContext MCP endpoint")
        endpoint = _endpoint(entry)
        saved = load_client_settings("minimax")
        allowed = bool(saved.get("allow_insecure_http")) and saved.get("server_url") == endpoint
        checks = server_diagnostics(server_url=endpoint, allow_insecure_http=allowed)

        async def probe():
            import httpx

            # Only the native configuration supplies headers; never inherit a global token.
            transport = httpx.AsyncHTTPTransport()
            async with (
                httpx.AsyncClient(
                    headers=entry.get("headers", {}), transport=transport, timeout=3, follow_redirects=False
                ) as http,
                PowerContextClient(endpoint, http_client=http, allow_insecure_http=allowed) as client,
            ):
                await client.get_server_info()

        try:
            asyncio.run(probe())
        except ServerResponseError as error:
            checks["server_access"] = Diagnostic(
                DiagnosticStatus.FAILED,
                f"CLI Server access returned HTTP {error.status_code}; check native MCP credentials and permissions",
            )
        except Exception:
            checks["server_access"] = Diagnostic(
                DiagnosticStatus.FAILED, "CLI could not check Server access using the native MCP configuration"
            )
        else:
            checks["server_access"] = Diagnostic(
                DiagnosticStatus.OK,
                "CLI reached Server info using native headers; MiniMax MCP connection and model calls were not tested",
            )
        return checks
