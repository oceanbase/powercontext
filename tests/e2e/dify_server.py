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

"""Disposable real HTTP/SQLite Server for Dify SDK acceptance tests."""

from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn
from pydantic import SecretStr

from powercontext.builtin.artifacts.experience import ExperienceContent
from powercontext.builtin.artifacts.generation import ArtifactGenerationInput
from powercontext.builtin.artifacts.handoff import HandoffDraft, HandoffGenerationRequest, HandoffStatement
from powercontext.builtin.artifacts.skill import SkillContent
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.server.factory import create_server_app
from powercontext.server.settings import BearerAuthConfig, McpConfig, ServerSettings

TOKEN = "disposable-dify-http-test"  # noqa: S105 - public disposable fixture credential


class ExperienceGenerator:
    async def generate(self, _value: ArtifactGenerationInput, /) -> ExperienceContent:
        return ExperienceContent(
            situation="A tool needs a bounded context.",
            action="Prepare context before composing the response.",
            outcome="The response includes scoped evidence.",
            lesson="Keep recall explicit and respect the configured Scope.",
        )


class SkillGenerator:
    async def generate(self, _value: ArtifactGenerationInput, /) -> SkillContent:
        return SkillContent(
            name="inspect-scoped-context",
            description="Use when continuing work with stored context.",
            instructions="Prepare the current Scope, inspect references, then continue the task.",
            validation=("Scope stays fixed.", "Exact references survive readback."),
        )


class HandoffPipeline:
    async def generate(self, request: HandoffGenerationRequest, /) -> HandoffDraft:
        citations = tuple(item.citation for item in request.evidence)
        return HandoffDraft(
            objective=request.objective,
            state=(HandoffStatement(text="中文交接状态已核实。", citations=citations),),
            disposition="continuable",
            next_action=HandoffStatement(text="Continue from the exact inspected evidence.", citations=citations),
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--database", required=True, type=Path)
    args = parser.parse_args()
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{args.database.as_posix()}"),
            workspace=args.database.parent,
            mcp=McpConfig(enabled=False),
            auth=BearerAuthConfig(enabled=True, token=SecretStr(TOKEN)),
        ),
        experience_generator=ExperienceGenerator(),
        skill_generator=SkillGenerator(),
        handoff_pipeline=HandoffPipeline(),
        scheduler_path=args.database.parent / "scheduler.db",
    )
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning", access_log=False)


if __name__ == "__main__":
    main()
