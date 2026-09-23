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

"""Install and diagnose the native OpenCode PowerContext plugin."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from shutil import which
from typing import cast
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import url2pathname, urlopen
from uuid import uuid4

from powercontext.cli.git_source import InvalidGitHubSourceError, clone_github_source, github_clone_url
from powercontext.cli.git_source import is_local_source as _is_local_source
from powercontext.cli.system import Diagnostic, DiagnosticStatus, SetupError
from powercontext.paths import powercontext_data_dir

from .hosts import host_adapter

OPENCODE_PLUGIN_NAME = "powercontext-opencode"
OPENCODE_PLUGIN_RELATIVE = Path("integrations") / "opencode" / "plugins" / "powercontext"
OPENCODE_BUNDLE = Path("lib") / "index.js"
OPENCODE_TUI_BUNDLE = Path("lib") / "tui.js"
OPENCODE_SKILL = Path("skills") / "powercontext-project-context" / "SKILL.md"
SKILL_MANIFEST = ".powercontext.json"
PLUGIN_MANIFEST = ".powercontext-opencode.json"
MINIMUM_VERSION = (1, 18, 21)
_VERSION = re.compile(r"^(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)")
_COMMIT = re.compile(r"^[0-9a-f]{40,64}$")
_ACTIVATION_PROBE_PATH = "POWERCONTEXT_OPENCODE_ACTIVATION_PROBE_PATH"
_ACTIVATION_PROBE_NONCE = "POWERCONTEXT_OPENCODE_ACTIVATION_PROBE_NONCE"
_ACTIVATION_PROBE_TIMEOUT = 15


@dataclass(frozen=True, slots=True)
class OpenCodeSetupResult:
    plugin: str
    plugin_path: str
    skill_path: str
    data_dir: str
    authorization_state: str = "not_attempted"


def opencode_executable() -> str:
    """Return a subprocess-launchable OpenCode CLI path."""

    executable = which("opencode")
    if executable is None:
        raise SetupError.unavailable("OpenCode CLI")
    return executable


def _version() -> str:
    value = _run_opencode("--version").strip()
    match = _VERSION.match(value)
    if match is None:
        raise SetupError.invalid_command_output([opencode_executable(), "--version"], "an invalid version")
    current = tuple(int(match.group(name)) for name in ("major", "minor", "patch"))
    if current[0] != 1 or current < MINIMUM_VERSION:
        raise SetupError(
            f"OpenCode v{value} is unsupported; PowerContext requires OpenCode v1.18.21 or newer in the 1.x line."
        )
    return value


def install_opencode_plugin(*, source: str, ref: str, server_url: str = "http://127.0.0.1:8000") -> OpenCodeSetupResult:
    """Install the plugin and its owned global Skill from one checkout."""

    opencode_executable()
    _version()
    data_dir = powercontext_data_dir()
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise SetupError(f"Cannot create PowerContext data directory {data_dir}: {error}") from error
    plugin_dir = resolve_opencode_plugin_dir(source=source, ref=ref)
    host_adapter("opencode").prepare(plugin_dir)
    require_complete_plugin(plugin_dir)
    config_dir = opencode_config_dir()
    plugin_target = config_dir / "plugins" / f"{OPENCODE_PLUGIN_NAME}.js"
    tui_source = plugin_dir / OPENCODE_TUI_BUNDLE
    legacy_tui_target = config_dir / "plugins" / f"{OPENCODE_PLUGIN_NAME}-tui.js"
    skill_target = config_dir / "skills" / "powercontext-project-context"
    require_replaceable_plugin(plugin_target)
    require_replaceable_plugin(legacy_tui_target)
    require_replaceable_skill(skill_target)
    tui_config = _prepare_tui_config(config_dir, tui_source)
    _install_plugin(plugin_dir / OPENCODE_BUNDLE, plugin_target)
    _commit_tui_config(tui_config)
    _install_skill(plugin_dir / OPENCODE_SKILL.parent, skill_target)
    try:
        legacy_tui_target.unlink(missing_ok=True)
    except OSError as error:
        raise SetupError.command_unavailable(["remove", "legacy", "OpenCode", "TUI plugin"], error) from error
    from .authorization import (
        configure_stored_authorization,
        setup_authorization_value,
    )

    return OpenCodeSetupResult(
        plugin=OPENCODE_PLUGIN_NAME,
        plugin_path=str(plugin_dir),
        skill_path=str(skill_target),
        data_dir=str(data_dir),
        authorization_state=configure_stored_authorization(
            "opencode",
            server_url=server_url,
            value=setup_authorization_value("opencode"),
        ),
    )


def resolve_opencode_plugin_dir(*, source: str, ref: str) -> Path:
    """Return the OpenCode package directory for a local checkout or Git ref."""

    if _is_local_source(source):
        return plugin_dir_from_checkout(Path(source).expanduser().resolve())
    return plugin_dir_from_checkout(_materialize_remote_checkout(source, ref))


def plugin_dir_from_checkout(root: Path) -> Path:
    if _is_opencode_plugin(root):
        return root
    plugin = root / OPENCODE_PLUGIN_RELATIVE
    if _is_opencode_plugin(plugin):
        return plugin
    raise SetupError.not_found("PowerContext OpenCode plugin", root)


def require_complete_plugin(path: Path) -> None:
    if (
        not (path / OPENCODE_BUNDLE).is_file()
        or not (path / OPENCODE_TUI_BUNDLE).is_file()
        or not (path / OPENCODE_SKILL).is_file()
    ):
        raise SetupError(
            f"PowerContext OpenCode plugin at {path} is missing lib/index.js, lib/tui.js, or powercontext-project-context Skill."
        )


def checkout_target(source: str, ref: str, resolved_commit: str) -> Path:
    root = (powercontext_data_dir() / "checkouts" / "opencode").resolve()
    if not ref or ref in {".", ".."} or "\x00" in ref:
        raise SetupError.invalid_ref("OpenCode", ref)
    try:
        normalized_source = _normalized_source_identity(source)
    except InvalidGitHubSourceError:
        raise SetupError.invalid_source("OpenCode") from None
    if _COMMIT.fullmatch(resolved_commit) is None:
        raise SetupError("failed to clone the GitHub source")
    source_key = hashlib.sha256(normalized_source.encode()).hexdigest()[:16]
    ref_key = hashlib.sha256(ref.encode()).hexdigest()[:16]
    target = (root / source_key / ref_key / resolved_commit).resolve()
    try:
        target.relative_to(root)
    except ValueError as error:
        raise SetupError.invalid_ref("OpenCode", ref) from error
    if target == root:
        raise SetupError.invalid_ref("OpenCode", ref)
    return target


def opencode_config_dir() -> Path:
    output = _run_opencode("debug", "paths")
    for line in output.splitlines():
        key, separator, value = line.strip().partition(" ")
        if key == "config" and separator and value.strip():
            return Path(value.strip()).expanduser().resolve()
    raise SetupError.invalid_command_output([opencode_executable(), "debug", "paths"], "no config path")


def require_replaceable_skill(target: Path) -> None:
    if target.exists() and not _owned_skill(target):
        raise SetupError(f"OpenCode Skill path {target} already exists and is not owned by PowerContext.")


def require_replaceable_plugin(target: Path) -> None:
    if target.exists() and not _owned_plugin(target):
        raise SetupError(f"OpenCode plugin path {target} already exists and is not owned by PowerContext.")


def _install_skill(source: Path, target: Path) -> None:
    require_replaceable_skill(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.parent / f".{target.name}.{uuid4().hex}.tmp"
    backup: Path | None = None
    try:
        shutil.copytree(source, staging)
        (staging / SKILL_MANIFEST).write_text(
            json.dumps({"schema": 1, "owner": "powercontext", "integration": "opencode"}, indent=2) + "\n",
            encoding="utf-8",
        )
        if target.exists():
            backup = target.parent / f".{target.name}.{uuid4().hex}.bak"
            os.replace(target, backup)
        try:
            os.replace(staging, target)
        except OSError:
            if backup is not None:
                os.replace(backup, target)
                backup = None
            raise
        if backup is not None:
            with suppress(OSError):
                shutil.rmtree(backup)
    except OSError as error:
        if staging.exists():
            shutil.rmtree(staging)
        if backup is not None and backup.exists() and not target.exists():
            with suppress(OSError):
                os.replace(backup, target)
        raise SetupError.command_unavailable(["install", "OpenCode", "Skill"], error) from error


def _owned_skill(path: Path) -> bool:
    try:
        payload = json.loads((path / SKILL_MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return payload == {"schema": 1, "owner": "powercontext", "integration": "opencode"}


def _plugin_manifest_path(path: Path) -> Path:
    return path.parent / PLUGIN_MANIFEST


def _owned_plugin(path: Path) -> bool:
    try:
        payload = json.loads(_plugin_manifest_path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return payload == {"schema": 1, "owner": "powercontext", "integration": "opencode-plugin"}


def _install_plugin(source: Path, target: Path) -> None:
    require_replaceable_plugin(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.parent / f".{target.name}.tmp"
    manifest = _plugin_manifest_path(target)
    manifest_staging = target.parent / f".{manifest.name}.tmp"
    try:
        shutil.copy2(source, staging)
        manifest_staging.write_text(
            json.dumps({"schema": 1, "owner": "powercontext", "integration": "opencode-plugin"}, indent=2) + "\n",
            encoding="utf-8",
        )
        # Install the ownership manifest first: an interruption then leaves a manifest without a
        # plugin, which the next setup run replaces instead of an unowned plugin conflict.
        os.replace(manifest_staging, manifest)
        os.replace(staging, target)
    except OSError as error:
        with suppress(OSError):
            staging.unlink()
        with suppress(OSError):
            manifest_staging.unlink()
        raise SetupError.command_unavailable(["install", "OpenCode", "plugin"], error) from error


def _tui_config_path(config_dir: Path) -> Path:
    for name in ("tui.json", "tui.jsonc"):
        candidate = config_dir / name
        if candidate.is_file():
            return candidate
    return config_dir / "tui.json"


def _tui_entry_path(entry: object) -> Path | None:
    spec = entry[0] if isinstance(entry, list) and entry else entry
    if not isinstance(spec, str):
        return None
    try:
        parsed = urlparse(spec)
        if parsed.scheme == "file":
            authority = f"//{parsed.netloc}" if parsed.netloc else ""
            raw = url2pathname(f"{authority}{parsed.path}")
        else:
            raw = spec
        return Path(raw).expanduser().resolve()
    except (OSError, RuntimeError, ValueError):
        return None


def _owns_tui_entry(entry: object, *, target: Path) -> bool:
    """Return whether one ``tui.json`` plugin entry belongs to this installation."""

    path = _tui_entry_path(entry)
    if path is None:
        return False
    if path == target or path.name in {f"{OPENCODE_PLUGIN_NAME}-tui.js", f"{OPENCODE_PLUGIN_NAME}.tui.js"}:
        return True
    if path.name != OPENCODE_TUI_BUNDLE.name:
        return False
    if _is_opencode_plugin(path.parent.parent):
        return True
    return _is_opencode_checkout_entry(path)


def _is_opencode_checkout_entry(path: Path) -> bool:
    """Recognize a stale TUI bundle from a remote checkout, deleted or not."""

    root = (powercontext_data_dir() / "checkouts" / "opencode").resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _read_tui_config(config_path: Path) -> tuple[dict[str, object], list[object]]:
    if config_path.exists():
        payload = json.loads(_strip_jsonc(config_path.read_text(encoding="utf-8")))
    else:
        payload = {"$schema": "https://opencode.ai/tui.json"}
    if not isinstance(payload, dict):
        raise TypeError
    typed_payload = cast(dict[str, object], payload)
    plugins_value = typed_payload.setdefault("plugin", [])
    if not isinstance(plugins_value, list):
        raise TypeError
    plugins = cast(list[object], plugins_value)
    return typed_payload, plugins


def _copy_string(text: str, start: int) -> int:
    length = len(text)
    end = start + 1
    while end < length:
        if text[end] == "\\":
            end += 2
            continue
        if text[end] == '"':
            return end + 1
        end += 1
    return length


def _skip_jsonc_space(text: str, index: int) -> int:
    """Advance past whitespace and comments to the next significant character."""

    length = len(text)
    while index < length:
        character = text[index]
        if character.isspace():
            index += 1
            continue
        if character == "/" and index + 1 < length:
            if text[index + 1] == "/":
                newline = text.find("\n", index + 2)
                index = length if newline == -1 else newline + 1
                continue
            if text[index + 1] == "*":
                end = text.find("*/", index + 2)
                index = length if end == -1 else end + 2
                continue
        break
    return index


def _matching_bracket(text: str, start: int) -> int | None:
    """Return the index of the ``]`` that closes the array opened at ``start``."""

    depth = 0
    index = start
    length = len(text)
    while index < length:
        character = text[index]
        if character == '"':
            index = _copy_string(text, index)
            continue
        if character == "/" and index + 1 < length and text[index + 1] in "/*":
            index = _skip_jsonc_space(text, index)
            continue
        if character in "[{":
            depth += 1
        elif character in "]}":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return None


def _plugin_array_span(text: str) -> tuple[int, int] | None:
    """Locate the top-level ``"plugin"`` array in the original JSONC text."""

    index = 0
    depth = 0
    length = len(text)
    while index < length:
        character = text[index]
        if character == '"':
            end = _copy_string(text, index)
            if depth == 1 and text[index:end] == '"plugin"':
                cursor = _skip_jsonc_space(text, end)
                if cursor < length and text[cursor] == ":":
                    cursor = _skip_jsonc_space(text, cursor + 1)
                    if cursor < length and text[cursor] == "[":
                        close = _matching_bracket(text, cursor)
                        if close is not None:
                            return cursor, close + 1
            index = end
            continue
        if character == "/" and index + 1 < length and text[index + 1] in "/*":
            index = _skip_jsonc_space(text, index)
            continue
        if character in "[{":
            depth += 1
        elif character in "}]":
            depth -= 1
        index += 1
    return None


def _rewrite_jsonc_plugin_array(text: str, plugins: list[object]) -> str | None:
    """Replace only the plugin array so surrounding JSONC comments survive."""

    span = _plugin_array_span(text)
    if span is None:
        return None
    start, end = span
    serialized = json.dumps(plugins, indent=2)
    key = text.rfind('"plugin"', 0, start)
    indent = ""
    if key >= 0:
        line_start = text.rfind("\n", 0, key) + 1
        candidate = text[line_start:key]
        if candidate and not candidate.strip():
            indent = candidate
    if indent:
        serialized = serialized.replace("\n", f"\n{indent}")
    return f"{text[:start]}{serialized}{text[end:]}"


def _strip_jsonc(text: str) -> str:
    # OpenCode accepts tui.jsonc, so comments and trailing commas are valid user
    # configuration. Strings are copied verbatim so "http://..." survives.
    without_comments: list[str] = []
    index = 0
    length = len(text)
    while index < length:
        character = text[index]
        if character == '"':
            end = _copy_string(text, index)
            without_comments.append(text[index:end])
            index = end
            continue
        if character == "/" and index + 1 < length and text[index + 1] == "/":
            newline = text.find("\n", index)
            index = length if newline == -1 else newline
            continue
        if character == "/" and index + 1 < length and text[index + 1] == "*":
            end = text.find("*/", index + 2)
            without_comments.append(" ")
            index = length if end == -1 else end + 2
            continue
        without_comments.append(character)
        index += 1
    stripped = "".join(without_comments)

    without_trailing_commas: list[str] = []
    index = 0
    length = len(stripped)
    while index < length:
        character = stripped[index]
        if character == '"':
            end = _copy_string(stripped, index)
            without_trailing_commas.append(stripped[index:end])
            index = end
            continue
        if character == ",":
            ahead = index + 1
            while ahead < length and stripped[ahead] in " \t\r\n":
                ahead += 1
            if ahead < length and stripped[ahead] in "}]":
                index += 1
                continue
        without_trailing_commas.append(character)
        index += 1
    return "".join(without_trailing_commas)


def _prepare_tui_config(config_dir: Path, plugin_path: Path) -> tuple[Path, str | None]:
    config_path = _tui_config_path(config_dir)
    try:
        payload, plugins = _read_tui_config(config_path)
    except (OSError, TypeError, ValueError) as error:
        raise SetupError.command_unavailable(["read", "OpenCode", "TUI config"], error) from error
    target = plugin_path.resolve()
    resolved = [entry for entry in plugins if not _owns_tui_entry(entry, target=target)]
    if not any(_tui_entry_path(entry) == target for entry in resolved):
        resolved.append(str(target))
    if resolved == plugins:
        return config_path, None
    payload["plugin"] = resolved
    if config_path.exists():
        original = config_path.read_text(encoding="utf-8")
        rewritten = _rewrite_jsonc_plugin_array(original, resolved)
        if rewritten is not None:
            return config_path, rewritten
    return config_path, json.dumps(payload, indent=2) + "\n"


def _commit_tui_config(prepared: tuple[Path, str | None]) -> None:
    config_path, serialized = prepared
    if serialized is None:
        return
    staging = config_path.with_name(f".{config_path.name}.tmp")
    try:
        staging.write_text(serialized, encoding="utf-8")
        os.replace(staging, config_path)
    except OSError as error:
        with suppress(OSError):
            staging.unlink()
        raise SetupError.command_unavailable(["install", "OpenCode", "TUI plugin"], error) from error


def _is_opencode_plugin(path: Path) -> bool:
    try:
        payload = json.loads((path / "package.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return payload.get("name") == OPENCODE_PLUGIN_NAME


def _materialize_remote_checkout(source: str, ref: str) -> Path:
    if not ref or ref in {".", ".."} or "\x00" in ref:
        raise SetupError.invalid_ref("OpenCode", ref)
    try:
        normalized_source = _normalized_source_identity(source)
    except InvalidGitHubSourceError:
        raise SetupError.invalid_source("OpenCode") from None
    root = (powercontext_data_dir() / "checkouts" / "opencode").resolve()
    source_key = hashlib.sha256(normalized_source.encode()).hexdigest()[:16]
    ref_key = hashlib.sha256(ref.encode()).hexdigest()[:16]
    staging_parent = root / source_key / ref_key
    staging_parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".checkout-", dir=staging_parent))
    try:
        clone_github_source(source, ref, staging)
        resolved_commit = _checkout_commit(staging)
        target = checkout_target(source, ref, resolved_commit)
        if _is_opencode_plugin(target) or _is_opencode_plugin(target / OPENCODE_PLUGIN_RELATIVE):
            return target
        _replace_checkout(staging, target)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return target


def _normalized_source_identity(source: str) -> str:
    clone_url = github_clone_url(source)
    if clone_url.startswith("git@github.com:"):
        repository = clone_url.removeprefix("git@github.com:")
    else:
        repository = urlparse(clone_url).path.lstrip("/")
    return f"github.com/{repository.removesuffix('.git').casefold()}"


def _checkout_commit(path: Path) -> str:
    command = ["git", "-C", str(path), "rev-parse", "--verify", "HEAD"]
    try:
        completed = subprocess.run(command, check=False, capture_output=True, text=True, timeout=10)  # noqa: S603
    except (OSError, subprocess.SubprocessError) as error:
        raise SetupError("failed to clone the GitHub source") from error
    commit = completed.stdout.strip().lower()
    if completed.returncode != 0 or _COMMIT.fullmatch(commit) is None:
        raise SetupError("failed to clone the GitHub source")
    return commit


def _replace_checkout(staging: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    backup = target.with_name(f".{target.name}.{uuid4().hex}.bak")
    moved_old = False
    try:
        if target.exists():
            os.replace(target, backup)
            moved_old = True
        os.replace(staging, target)
    except BaseException:
        if moved_old and not target.exists() and backup.exists():
            os.replace(backup, target)
        raise
    finally:
        if backup.exists() and target.exists():
            shutil.rmtree(backup)


def _configured_plugin(output: str) -> bool:
    payload = _debug_config_payload(output)
    if payload is None:
        return False
    plugins = payload.get("plugin") if isinstance(payload, dict) else None
    if not isinstance(plugins, list):
        return False
    for entry in plugins:
        spec = entry[0] if isinstance(entry, list) and entry else entry
        if not isinstance(spec, str):
            continue
        parsed = urlparse(spec)
        if parsed.scheme == "file":
            authority = f"//{parsed.netloc}" if parsed.netloc else ""
            path = Path(url2pathname(f"{authority}{parsed.path}"))
        else:
            path = Path(spec)
        if _is_opencode_plugin(path) or _is_opencode_plugin(path.parent):
            return True
    return False


def _debug_config_payload(output: str) -> dict[str, object] | None:
    """Extract OpenCode's config when a loaded plugin writes to stdout first."""

    decoder = json.JSONDecoder()
    for index, character in enumerate(output):
        if character != "{":
            continue
        try:
            payload, _ = decoder.raw_decode(output, index)
        except ValueError:
            continue
        if isinstance(payload, dict) and isinstance(payload.get("plugin"), list):
            return payload
    return None


def run_opencode_diagnostics() -> dict[str, Diagnostic]:
    """Collect model-free diagnostics for the optional OpenCode integration."""

    try:
        executable = opencode_executable()
        actual = _version()
    except SetupError as error:
        return {
            "opencode": Diagnostic(status=DiagnosticStatus.FAILED, detail=str(error)),
            "plugin": Diagnostic(status=DiagnosticStatus.SKIPPED, detail="not checked because OpenCode is unavailable"),
            "skill": Diagnostic(status=DiagnosticStatus.SKIPPED, detail="not checked because OpenCode is unavailable"),
        }
    try:
        config_dir = opencode_config_dir()
        output, activated = _probe_plugin_activation()
        configured = _configured_plugin(output) or _owned_plugin(config_dir / "plugins" / f"{OPENCODE_PLUGIN_NAME}.js")
    except SetupError as error:
        return {
            "opencode": Diagnostic(status=DiagnosticStatus.OK, detail=f"{executable} ({actual})"),
            "plugin": Diagnostic(status=DiagnosticStatus.FAILED, detail=str(error)),
            "skill": Diagnostic(status=DiagnosticStatus.SKIPPED, detail="not checked because config is unavailable"),
        }
    skill = config_dir / "skills" / "powercontext-project-context"
    skill_ok = _owned_skill(skill) and (skill / "SKILL.md").is_file()
    return {
        "opencode": Diagnostic(status=DiagnosticStatus.OK, detail=f"{executable} ({actual})"),
        "plugin": Diagnostic(
            status=DiagnosticStatus.OK if configured and activated else DiagnosticStatus.FAILED,
            detail=(
                f"{OPENCODE_PLUGIN_NAME} is configured and active"
                if configured and activated
                else (
                    "PowerContext OpenCode plugin is configured but did not activate"
                    if configured
                    else "PowerContext OpenCode plugin is not configured"
                )
            ),
        ),
        "skill": Diagnostic(
            status=DiagnosticStatus.OK if skill_ok else DiagnosticStatus.FAILED,
            detail=(str(skill) if skill_ok else "PowerContext OpenCode Skill is not installed"),
        ),
    }


def _probe_plugin_activation() -> tuple[str, bool]:
    token = uuid4().hex
    with tempfile.TemporaryDirectory(prefix="powercontext-opencode-probe-") as directory:
        path = Path(directory) / "active"
        port = _available_local_port()
        output = _run_opencode("debug", "config")
        command = [
            opencode_executable(),
            "serve",
            "--hostname",
            "127.0.0.1",
            "--port",
            str(port),
        ]
        try:
            # `debug config` only resolves configuration and does not load plugins.
            # Start a short-lived headless server so OpenCode initializes the plugin
            # lifecycle without creating a session or invoking a model.
            _run_opencode_probe(
                command,
                {
                    _ACTIVATION_PROBE_PATH: str(path),
                    _ACTIVATION_PROBE_NONCE: token,
                },
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise SetupError.command_unavailable(command, error) from error
        try:
            activated = path.read_text(encoding="utf-8") == token
        except OSError:
            activated = False
    return output, activated


def _run_opencode_probe(command: list[str], env: dict[str, str]) -> None:
    process: subprocess.Popen[str] | None = None
    try:
        process = subprocess.Popen(  # noqa: S603 - command uses the fixed OpenCode executable and literal arguments.
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            env=os.environ | env,
        )
        port = int(command[command.index("--port") + 1])
        deadline = time.monotonic() + _ACTIVATION_PROBE_TIMEOUT
        probe_path = Path(env[_ACTIVATION_PROBE_PATH])
        nonce = env[_ACTIVATION_PROBE_NONCE]
        while time.monotonic() < deadline:
            if probe_path.exists():
                try:
                    if probe_path.read_text(encoding="utf-8") == nonce:
                        return
                except OSError:
                    pass
            if process.poll() is not None:
                return
            try:
                with urlopen(f"http://127.0.0.1:{port}/session", timeout=1) as response:
                    response.read(1)
            except (OSError, URLError):
                pass
            time.sleep(0.05)
        raise subprocess.TimeoutExpired(command, _ACTIVATION_PROBE_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as error:
        raise SetupError.command_unavailable(command, error) from error
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def _available_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _run_opencode(*arguments: str, env: dict[str, str] | None = None) -> str:
    command = [opencode_executable(), *arguments]
    try:
        completed = subprocess.run(  # noqa: S603 - arguments are passed directly to the fixed OpenCode executable.
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            env=None if env is None else os.environ | env,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise SetupError.command_unavailable(command, error) from error
    if completed.returncode != 0:
        detail = (
            (completed.stderr or "").strip() or (completed.stdout or "").strip() or f"exit code {completed.returncode}"
        )
        raise SetupError.command_failed(command, detail)
    return completed.stdout or ""


__all__ = [
    "OPENCODE_PLUGIN_NAME",
    "OpenCodeSetupResult",
    "checkout_target",
    "install_opencode_plugin",
    "opencode_config_dir",
    "opencode_executable",
    "plugin_dir_from_checkout",
    "require_complete_plugin",
    "resolve_opencode_plugin_dir",
    "run_opencode_diagnostics",
]
