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

"""Isolated Artifact declaration validation without starting the generation runtime."""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import inspect
import json
import re
import sys
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Annotated, Any

import yaml
from jsonschema import Draft202012Validator
from pydantic import BaseModel, ConfigDict, Field, JsonValue, SecretBytes, SecretStr, field_validator

from powercontext.artifact_plugins import InputContract
from powercontext.artifacts.indexes import ArtifactSearchDeclaration, validate_index_schema
from powercontext.sources import SourceProjectionKey, SourceRef

_MAX_INPUT_BYTES = 1024 * 1024
_MAX_OUTPUT_BYTES = 1024 * 1024
_BUILTIN_FAMILIES = frozenset({
    "memory",
    "atomic-memory",
    "experience",
    "skill",
    "handoff",
    "profile",
    "prompt",
    "topic-memory",
})


class _ProcessingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, allow_inf_nan=False)

    interval_seconds: float = Field(gt=0)
    max_workers: int = Field(ge=1)
    timeout_seconds: float = Field(gt=0)

    @field_validator("interval_seconds")
    @classmethod
    def representable_interval(cls, value: float) -> float:
        try:
            interval = timedelta(seconds=value)
        except OverflowError as error:
            raise ValueError("interval_seconds exceeds the supported duration") from error  # noqa: TRY003
        if interval.total_seconds() <= 0:
            raise ValueError("interval_seconds must be at least one microsecond")  # noqa: TRY003
        return value


class _PluginManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,127}$")
    version: str = Field(min_length=1, max_length=128, pattern=r".*\S.*")
    entry: str = Field(min_length=3, max_length=4096)
    binding_name: str = Field(pattern=r"^[a-z][a-z0-9-]{0,127}$")
    scope_ids: list[Annotated[str, Field(min_length=1, max_length=256)]] | None = Field(
        default=None, min_length=1, max_length=1000
    )
    max_sources: int = Field(ge=1)
    processing: _ProcessingConfig
    config: dict[str, JsonValue]

    @field_validator("scope_ids")
    @classmethod
    def unique_scopes(cls, value: list[str] | None) -> list[str] | None:
        if value is not None and len(set(value)) != len(value):
            raise ValueError("Scope IDs must be unique")  # noqa: TRY003
        return None if value is None else sorted(value)


def read_manifest(directory: str) -> _PluginManifest:
    path = Path(directory) / "plugin.yml"
    if path.stat().st_size > _MAX_INPUT_BYTES:
        raise ValueError("plugin manifest is too large")  # noqa: TRY003
    return _PluginManifest.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def code_fingerprint(directory: str) -> str:
    root = Path(directory)
    digest = hashlib.sha256()
    files = sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and not {"__pycache__", ".venv", ".git"}.intersection(path.relative_to(root).parts)
    )
    for path in files:
        digest.update(str(path.relative_to(root)).encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def load_plugin(directory: str, manifest: _PluginManifest):
    root = Path(directory)
    entry, factory = manifest.entry.rsplit(":", 1)
    path = (root / entry).resolve()
    if Path(entry).is_absolute() or not path.is_relative_to(root.resolve()) or path.suffix != ".py":
        raise ValueError("entry must be a Python file inside the plugin directory")  # noqa: TRY003
    module_name = "powercontext_user_artifact_plugin"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ValueError("plugin entry is unavailable")  # noqa: TRY003
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    sys.path.insert(0, str(root))
    spec.loader.exec_module(module)
    module.__dict__["create_pipeline"] = getattr(module, factory)
    return module


def describe_plugin(directory: str, snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    manifest = read_manifest(directory) if snapshot is None else _PluginManifest.model_validate(snapshot)
    return describe_module(load_plugin(directory, manifest), manifest, directory)


def _input_declaration(inputs: InputContract) -> dict[str, Any]:
    if not isinstance(inputs, InputContract) or not isinstance(inputs.projections, dict) or not inputs.projections:
        raise ValueError("inputs must declare a nonempty Source projection mapping")  # noqa: TRY003
    projections = {}
    for name, projection in inputs.projections.items():
        SourceRef(source_type=name, source_id="validation")
        projections[name] = SourceProjectionKey.model_validate(projection).model_dump(mode="json")
    families = _read_families(inputs.artifact_families)
    return {"projections": projections, **({"artifact_families": families} if families else {})}


def _read_families(families):
    if not isinstance(families, tuple):
        raise TypeError("read families must be a tuple")  # noqa: TRY003
    for family in families:
        if not isinstance(family, str) or re.fullmatch(r"[a-z][a-z0-9-]{0,127}", family) is None or family == "memory":
            raise ValueError("read families must name canonical Artifact families")  # noqa: TRY003
    if len(set(families)) != len(families):
        raise ValueError("read families must be distinct")  # noqa: TRY003
    return sorted(families)


def _configuration_json(value, serialized):
    # Config stays private to management/Workers. Preserve credentials instead of
    # persisting SecretStr's display mask, while retaining JSON normalization.
    if isinstance(value, SecretStr):
        return value.get_secret_value()
    if isinstance(value, SecretBytes):
        return value.get_secret_value().decode("utf-8")
    if isinstance(value, dict) and isinstance(serialized, dict):
        return {key: _configuration_json(value[key], item) for key, item in serialized.items()}
    if isinstance(value, (list, tuple)) and isinstance(serialized, list):
        return [_configuration_json(original, item) for original, item in zip(value, serialized, strict=True)]
    return serialized


def _validate_factory(factory) -> None:
    if not inspect.iscoroutinefunction(factory):
        raise ValueError("plugin factory must be async")  # noqa: TRY003
    try:
        inspect.signature(factory).bind(None, None)
    except (TypeError, ValueError) as error:
        raise ValueError("plugin factory must accept context and configuration") from error  # noqa: TRY003


def describe_module(module, manifest: _PluginManifest, directory: str) -> dict[str, Any]:
    if module.plugin_api_version != "1" or len(module.families) != 1:
        raise ValueError("API version 1 requires exactly one output family")  # noqa: TRY003
    _validate_factory(module.create_pipeline)
    family = module.families[0]
    if re.fullmatch(r"[a-z][a-z0-9-]{0,127}", family.family) is None or family.family in _BUILTIN_FAMILIES:
        raise ValueError("a plugin needs a custom canonical family")  # noqa: TRY003
    if not family.schema_version or len(family.schema_version) > 128:
        raise ValueError("a bounded schema_version is required")  # noqa: TRY003
    schema = family.content_model.model_json_schema()
    Draft202012Validator.check_schema(schema)
    if schema.get("type") != "object":
        raise ValueError("Artifact content must be a JSON object")  # noqa: TRY003
    fields = ()
    search = family.search
    if search is not None:
        search = ArtifactSearchDeclaration.model_validate(search)
        for index in search.indexes:
            validate_index_schema(index, schema)
    # JSON validation restores strict Config types from the serialized definition snapshot.
    validated_config = module.Config.model_validate_json(json.dumps(manifest.config, ensure_ascii=False))
    snapshot = manifest.model_dump(mode="json")
    snapshot["config"] = _configuration_json(
        validated_config.model_dump(mode="python", by_alias=True),
        validated_config.model_dump(mode="json", by_alias=True),
    )
    return {
        "manifest": snapshot,
        "declaration": {
            "plugin_id": manifest.id,
            "version": manifest.version,
            "family": family.family,
            "schema_version": family.schema_version,
            "content_schema": schema,
            "search_fields": list(fields),
            **({"search": search.model_dump(mode="json")} if search is not None else {}),
            **_input_declaration(module.inputs),
            "fingerprint": code_fingerprint(directory),
        },
    }


async def validate_plugin(directory: str, snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    # Result files keep user import-time stdout out of the declaration protocol.
    with TemporaryDirectory(prefix="powercontext-artifact-validation-") as temporary:
        root = Path(temporary)
        (root / "manifest.json").write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            __name__,
            directory,
            str(root / "manifest.json"),
            str(root / "result.json"),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            async with asyncio.timeout(15):
                await process.wait()
        except BaseException:
            if process.returncode is None:
                process.kill()
            await process.wait()
            raise
        if process.returncode:
            raise ValueError("plugin declaration or configuration validation failed")  # noqa: TRY003
        output = root / "result.json"
        if output.stat().st_size > _MAX_OUTPUT_BYTES:
            raise ValueError("plugin declaration is too large")  # noqa: TRY003
        return json.loads(output.read_text())


if __name__ == "__main__":
    result = describe_plugin(sys.argv[1], json.loads(Path(sys.argv[2]).read_text()))
    Path(sys.argv[3]).write_text(json.dumps(result, ensure_ascii=False))
