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

"""Load trusted local code on the execution machine, never in the manager."""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import inspect
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

from pydantic import BaseModel

from powercontext.source_management.config import RuntimeConfig
from powercontext.source_management.errors import PluginError
from powercontext.source_management.models import DYNAMIC_PLUGIN_API_VERSION, Capability
from powercontext.sources import SourceDefinitionRegistry


@dataclass(frozen=True)
class LoadedRuntimePlugin:
    module: ModuleType
    config: BaseModel
    registry: SourceDefinitionRegistry
    connector_name: str
    connector_version: str
    capabilities: tuple[Capability, ...]


def load_local_module(directory: Path, entry: str) -> ModuleType:
    path = (directory / entry).resolve()
    if path.is_dir():
        path /= "__init__.py"
    if path.suffix != ".py" or not path.is_file():
        if "/" not in entry and "\\" not in entry and not entry.endswith(".py"):
            return importlib.import_module(entry)
        raise PluginError("plugin-entry", "The trusted local Python entry does not exist")
    name = "_pc_managed_" + hashlib.sha256(str(path).encode()).hexdigest()
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise PluginError("plugin-entry", "Could not load the local Python entry")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        for key in tuple(sys.modules):
            if key == name or key.startswith(name + "."):
                del sys.modules[key]
        raise
    return module


def load_runtime_plugin(config: RuntimeConfig) -> LoadedRuntimePlugin:
    module = load_local_module(config.plugin_dir, config.plugin)
    if getattr(module, "plugin_api_version", None) != DYNAMIC_PLUGIN_API_VERSION:
        raise PluginError("plugin-version", "Managed dynamic plugins require plugin_api_version = '2'")
    configuration = getattr(module, "Config", None)
    if not isinstance(configuration, type) or not issubclass(configuration, BaseModel):
        raise PluginError("plugin-contract", "Config must be a Pydantic model")
    if not inspect.iscoroutinefunction(getattr(module, "apply", None)):
        raise PluginError("plugin-contract", "apply must be an async function")
    definitions = getattr(module, "definitions", None)
    if not isinstance(definitions, tuple) or not definitions:
        raise PluginError("plugin-contract", "definitions must be a non-empty tuple")
    capabilities = tuple(
        capability
        for value in getattr(module, "capabilities", ())
        if (capability := Capability.model_validate(value)).kind == "collect"
    )
    if not capabilities:
        raise PluginError("plugin-contract", "Managed plugins must declare capabilities")
    if any(c.kind == "collect" for c in capabilities) and not inspect.iscoroutinefunction(
        getattr(module, "identify_target", None)
    ):
        raise PluginError("plugin-contract", "collect requires async identify_target(request, config)")
    identities = []
    for field in ("connector_name", "connector_version"):
        value = getattr(module, field, None)
        if not isinstance(value, str) or not value or value != value.strip() or len(value) > 128:
            raise PluginError("plugin-contract", "Connector identities must be non-empty trimmed strings")
        identities.append(value)
    return LoadedRuntimePlugin(
        module,
        configuration.model_validate(config.config),
        SourceDefinitionRegistry(definitions),
        identities[0],
        identities[1],
        capabilities,
    )
