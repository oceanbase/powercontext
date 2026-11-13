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

"""Plugin declarations and module loading shared by the Server check and Workers.

A plugin is a directory named after the plugin that contains ``plugin.py``. Importing
it must assign an ``ArtifactPlugin`` to ``plugin``. The Server imports user code only
in a child process; the declaration crosses process boundaries as ``PluginManifest``.
"""

from __future__ import annotations

import hashlib
import importlib.util
import inspect
import json
import re
import sys
from pathlib import Path
from types import ModuleType

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from powercontext.plugins import ArtifactPlugin, ArtifactPluginSpec

ENTRY_FILE = "plugin.py"
_NAME = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
# MCP clients commonly reject longer tool names.
MCP_TOOL_NAME_LIMIT = 64


class PluginConfigError(ValueError):
    """A plugin directory or its declaration cannot be loaded."""


class PluginManifest(BaseModel):
    """The picklable, code-free declaration of one loaded plugin."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(pattern=_NAME.pattern)
    spec: ArtifactPluginSpec
    # Content digest of the plugin directory when it was checked; see ``code_fingerprint``.
    fingerprint: str = ""
    # Declarative manifests of the Source Definitions the plugin submits through its routes.
    source_manifests: tuple[dict[str, JsonValue], ...] = ()
    # Digest of everything above except the fingerprint: whether a new version changes what core registers.
    declaration_digest: str = ""
    upgrade: bool = False

    @property
    def binding_name(self) -> str:
        return f"plugin:{self.name}"


def mcp_tool_name(plugin: str, handler: str) -> str:
    # Plugin names have no underscores, so the first double underscore always ends the plugin name.
    return f"{plugin}__{handler}"


def code_fingerprint(directory: Path) -> str:
    """SHA-256 over relative paths and contents, so any content change shows, whatever the timestamps."""

    root = directory.resolve()
    digest = hashlib.sha256()
    files = sorted(
        path for path in root.rglob("*") if path.is_file() and "__pycache__" not in path.relative_to(root).parts
    )
    for path in files:
        digest.update(str(path.relative_to(root)).encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def load_module(directory: Path, name: str) -> ModuleType:
    root = directory.resolve()
    path = root / ENTRY_FILE
    if not path.is_file():
        raise PluginConfigError(f"the plugin directory has no {ENTRY_FILE}")  # noqa: TRY003
    module_name = f"powercontext_plugin_{name.replace('-', '_')}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise PluginConfigError("plugin entry is unavailable")  # noqa: TRY003
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    # Plugins may import sibling modules from their own directory.
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    spec.loader.exec_module(module)
    return module


def load_plugin(directory: Path, name: str | None = None) -> tuple[PluginManifest, ArtifactPlugin]:
    """Import ``plugin.py`` and validate its declaration without calling the factory.

    ``name`` defaults to the directory name; a version snapshot passes the plugin's name.
    """

    name = directory.name if name is None else name
    if _NAME.fullmatch(name) is None:
        raise PluginConfigError("the directory name is not a valid plugin name")  # noqa: TRY003
    declaration = getattr(load_module(directory, name), "plugin", None)
    if not isinstance(declaration, ArtifactPlugin):
        raise PluginConfigError(f"{ENTRY_FILE} must assign an ArtifactPlugin to plugin")  # noqa: TRY003
    if not inspect.iscoroutinefunction(declaration.setup):
        raise PluginConfigError("setup must be an async function")  # noqa: TRY003
    if declaration.upgrade is not None and not inspect.iscoroutinefunction(declaration.upgrade):
        raise PluginConfigError("upgrade must be an async function")  # noqa: TRY003
    try:
        spec = ArtifactPluginSpec.model_validate(declaration.model_dump(mode="json"))
    except ValidationError as error:
        fields = ", ".join(".".join(str(part) for part in item["loc"]) or "plugin" for item in error.errors())
        raise PluginConfigError(f"invalid declaration fields: {fields}") from error  # noqa: TRY003
    _check_routes_and_pages(directory, name, spec)
    from powercontext.sources.observations import manifest_for_definition

    try:
        source_manifests = tuple(
            manifest_for_definition(definition).model_dump(mode="json", by_alias=True)
            for definition in declaration.definitions
        )
    except Exception as error:
        raise PluginConfigError("invalid Source Definitions") from error  # noqa: TRY003
    declared = {
        "name": name,
        "spec": spec.model_dump(mode="json"),
        "source_manifests": source_manifests,
        "upgrade": declaration.upgrade is not None,
    }
    return (
        PluginManifest(
            name=name,
            spec=spec,
            fingerprint=code_fingerprint(directory),
            source_manifests=source_manifests,
            declaration_digest=hashlib.sha256(json.dumps(declared, sort_keys=True).encode()).hexdigest(),
            upgrade=declaration.upgrade is not None,
        ),
        declaration,
    )


def _check_routes_and_pages(directory: Path, name: str, spec: ArtifactPluginSpec) -> None:
    for route in () if spec.api is None else spec.api.routes:
        if route.mcp and len(mcp_tool_name(name, route.handler)) > MCP_TOOL_NAME_LIMIT:
            raise PluginConfigError(f"MCP tool names are at most {MCP_TOOL_NAME_LIMIT} characters")  # noqa: TRY003
    if spec.pages is not None:
        pages = (directory.resolve() / spec.pages.directory).resolve()
        if not pages.is_relative_to(directory.resolve()) or not (pages / spec.pages.entry).is_file():
            raise PluginConfigError("pages.entry must be a file inside pages.directory")  # noqa: TRY003


def main() -> None:
    """Check one plugin directory in a child process and report the outcome as JSON."""

    try:
        manifest, _ = load_plugin(Path(sys.argv[1]), sys.argv[2] if len(sys.argv) > 2 else None)
    except Exception as error:
        message = (
            str(error)
            if isinstance(error, PluginConfigError)
            else f"import or validation failed: {type(error).__name__}"
        )
        print(json.dumps({"error": message}))
        return
    print(json.dumps({"manifest": manifest.model_dump(mode="json")}))


if __name__ == "__main__":
    main()
