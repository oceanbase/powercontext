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

"""Opt-in Codex history collection through the managed Source plugin contract."""

from __future__ import annotations

import asyncio
from hashlib import sha256
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

from powercontext.builtin.sources import CONTENT_SOURCE_DEFINITION, CONTENT_SOURCE_NAME, ContentCapture
from powercontext.redaction import known_secret_values, redact_known_secrets
from powercontext.source_management import Capability, TargetIdentity
from powercontext.source_management.models import ManagementError, SourceTarget
from powercontext.sources import ConnectorRunCompletion, ConnectorRunStatus, ConnectorSubmissionStatus

from .reader import SessionImportError, _metadata, _read_codex_session_file

plugin_api_version = "2"
connector_name = "codex-history"
connector_version = "1"
definitions = (CONTENT_SOURCE_DEFINITION,)


class SessionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_file: str = Field(
        min_length=1, description="JSONL path relative to the configured Codex sessions directory"
    )


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")

    codex_home: Path
    poll_interval: float = Field(default=30, gt=0)

    @field_validator("codex_home")
    @classmethod
    def absolute_home(cls, value: Path) -> Path:
        value = value.expanduser()
        if not value.is_absolute():
            raise ValueError("codex_home must be an absolute path on the execution machine")  # noqa: TRY003
        return value


capabilities = (
    Capability(
        name="codex.session-history",
        version="1",
        description="Import real user prompts from one explicitly selected Codex session into the target Scope",
        input_schema=SessionInput.model_json_schema(),
    ),
)


def session_path(config: Config, value: str) -> tuple[Path, Path]:
    root = config.codex_home.expanduser().resolve() / "sessions"
    relative = Path(value)
    path = (root / relative).resolve()
    if relative.is_absolute() or not path.is_relative_to(root) or path.suffix != ".jsonl":
        raise ValueError("Session file must be a JSONL path inside the configured sessions directory")  # noqa: TRY003
    return root, path


async def identify_target(request, config):
    value = SessionInput.model_validate(request.input)
    root, path = session_path(config, value.session_file)
    relative = path.relative_to(root).as_posix()
    return TargetIdentity(target_key=sha256(relative.encode()).hexdigest(), normalized_input={"session_file": relative})


class CodexHistoryConnector:
    name = connector_name
    version = connector_version
    source_definitions = frozenset({CONTENT_SOURCE_NAME})

    def __init__(self, ctx, config):
        self.ctx, self.config = ctx, config
        self.targets: tuple[SourceTarget, ...] = ()

    async def run(self, session):
        checkpoint = dict(session.checkpoint or {})
        # A checkpoint is already scoped by deployment, instance and Scope in the framework.
        accepted = set(checkpoint.get("accepted", []))
        complete = True
        secrets = known_secret_values(codex_home=self.config.codex_home)
        attempted: set[str] = set()
        for target in self.targets:
            try:
                root, path = session_path(self.config, SessionInput.model_validate(target.input).session_file)
                prompts = await asyncio.to_thread(_read_codex_session_file, path, session_root=root)
            except (SessionImportError, OSError, ValueError):
                session.fail("session-" + target.target_id, CONTENT_SOURCE_NAME, "session_unreadable")
                complete = False
                continue
            # Decode the whole selected file before submitting, so a malformed tail cannot
            # turn a partial read into a completed import. No other session is scanned.
            for prompt in prompts:
                source_id = prompt.source_id_for_scope(session.binding.scope_id)
                if source_id in accepted:
                    continue
                if source_id in attempted:
                    continue
                content = redact_known_secrets(prompt.content, secrets=secrets).strip()
                if not content or len(content) > 200_000:
                    continue
                attempted.add(source_id)
                receipt = await self.ctx.targets.submit(
                    target,
                    session=session,
                    item_id=source_id,
                    definition_name=CONTENT_SOURCE_NAME,
                    value=ContentCapture(source_id=source_id, content=content, metadata=_metadata(prompt)),
                )
                if receipt.status != ConnectorSubmissionStatus.ACCEPTED:
                    complete = False
                    continue
                accepted.add(source_id)
        return ConnectorRunCompletion(
            status=ConnectorRunStatus.COMPLETE if complete else ConnectorRunStatus.INCOMPLETE,
            checkpoint={"accepted": sorted(accepted)},
        )


async def apply(ctx, config):
    connector = CodexHistoryConnector(ctx, config)
    ctx.connect(connector)
    while True:
        targets = await ctx.targets.list()
        for scope_id in sorted({target.scope_id for target in targets}):
            selected = tuple(target for target in targets if target.scope_id == scope_id)
            connector.targets = selected
            try:
                await ctx.run_batch(scope_id=scope_id, targets=selected)
            except ManagementError as error:
                if error.code != "stale-target":
                    raise
                break
        await asyncio.sleep(config.poll_interval)
