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

"""Opt-in Codex session history Source collector."""

from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
from pathlib import Path
from typing import cast

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from powercontext.plugins import (
    Collect,
    CollectedItem,
    CollectRequest,
    CollectResult,
    Interval,
    SourcePlugin,
    TargetSpec,
)
from powercontext.redaction import known_secret_values, redact_known_secrets
from powercontext.sources import TEXT_EVIDENCE_PROJECTION_KEY, Source, SourceMaterialization, TextEvidence


def _sibling(filename: str, module_name: str):
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError from None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


_reader = _sibling("reader.py", "powercontext_codex_history_reader")
SessionImportError = _reader.SessionImportError
_metadata = _reader._metadata
_read_codex_session_file = _reader._read_codex_session_file


class SessionTarget(BaseModel):
    # Ignore fields added by a newer Server so older runners can continue.
    model_config = ConfigDict(extra="ignore")
    session_file: str = Field(min_length=1, description="JSONL file relative to CODEX_HOME/sessions")


class SessionCapture(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    source_id: str = Field(min_length=1)
    content: str = Field(min_length=1, max_length=200_000)
    metadata: dict[str, JsonValue]


class SessionSource(Source):
    capture: SessionCapture


class SessionTextEvidence:
    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = SessionSource
    output_class = TextEvidence

    def project(self, source: SessionSource, /) -> TextEvidence:
        return TextEvidence(
            source_type="codex-session-prompt",
            source_id=source.name,
            content=source.capture.content,
            metadata=source.capture.metadata,
        )


class SessionDefinition:
    name = "codex-session-prompt"
    version = "1"
    input_class = SessionCapture
    source_class = SessionSource
    projections = (SessionTextEvidence(),)

    async def resolve(self, value: SessionCapture, /) -> SessionSource:
        return SessionSource(
            name=value.source_id,
            materialization=SourceMaterialization.CAPTURED,
            description="Codex user prompt",
            capture=value,
        )

    async def read(self, source: SessionSource, /) -> SessionCapture:
        return source.capture


SESSION_DEFINITION = SessionDefinition()


class CodexHistoryCollector:
    async def collect(self, request: CollectRequest) -> CollectResult:
        result = CollectResult()
        codex_home = await asyncio.to_thread(_codex_home)
        session_root = codex_home / "sessions"
        secrets = await asyncio.to_thread(known_secret_values, codex_home=codex_home)
        remaining = request.max_items
        for target in request.targets:
            settings = SessionTarget.model_validate(target.parameters)
            try:
                path = await asyncio.to_thread(_session_path, session_root, settings.session_file)
            except ValueError:
                result.fail(target, code="invalid_session_path", message="Select a JSONL file inside sessions")
                continue
            try:
                prompts = await asyncio.to_thread(_read_codex_session_file, path, session_root=session_root)
            except (SessionImportError, OSError, ValueError) as error:
                result.fail(target, code="session_unreadable", message=type(error).__name__)
                continue
            checkpoint = _checkpoint_ids(target.checkpoint)
            items = []
            queued: set[str] = set()
            has_more = False
            for prompt in prompts:
                content = redact_known_secrets(prompt.content, secrets=secrets).strip()
                if not content or len(content) > 200_000:
                    continue
                # Hash the captured content: if redaction changes a prompt already stored by
                # the live hook, retain both immutable records instead of conflicting writes.
                source_id = prompt.source_id_for_scope(request.scope_id, content=content)
                if source_id in checkpoint or source_id in queued:
                    continue
                items.append(
                    CollectedItem(
                        definition=SESSION_DEFINITION.name,
                        value=SessionCapture(
                            source_id=source_id, content=content, metadata=cast(dict[str, JsonValue], _metadata(prompt))
                        ),
                    )
                )
                checkpoint.add(source_id)
                queued.add(source_id)
                if len(items) >= remaining:
                    has_more = True
                    break
            result.add(target, items, checkpoint=sorted(checkpoint), has_more=has_more)
            remaining -= len(items)
            if remaining <= 0:
                break
        return result


async def setup(ctx):
    return CodexHistoryCollector()


def _codex_home() -> Path:
    return Path(os.getenv("CODEX_HOME", str(Path.home() / ".codex"))).expanduser().resolve()


def _session_path(session_root: Path, session_file: str) -> Path:
    relative = Path(session_file)
    path = (session_root / relative).resolve()
    if relative.is_absolute() or not path.is_relative_to(session_root) or path.suffix != ".jsonl":
        raise ValueError
    return path


def _checkpoint_ids(value: JsonValue | None) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {item for item in value if isinstance(item, str)}


plugin = SourcePlugin(
    version="2.0.0",
    min_compatible_version="2.0.0",
    definitions=(SESSION_DEFINITION,),
    collect=Collect(schedule=Interval(seconds=30)),
    target=TargetSpec(
        description="Collect one explicitly selected Codex session file from CODEX_HOME/sessions",
        parameters=SessionTarget,
        identity={"session_file": ("strip",)},
    ),
    setup=setup,
)
