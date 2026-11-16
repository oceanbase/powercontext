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

"""Local ACP stdio agent for testing Harbor's actual configured-model runner."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from acp import Agent, run_agent
from acp.schema import (
    InitializeResponse,
    ModelInfo,
    NewSessionResponse,
    PromptResponse,
    SessionModelState,
    SetSessionModelResponse,
)


class ModelAgent(Agent):
    def __init__(self) -> None:
        self.selected: str | None = None

    async def initialize(self, protocol_version, **_kwargs):
        return InitializeResponse(protocol_version=protocol_version)

    async def new_session(self, **_kwargs):
        return NewSessionResponse(
            session_id="fixture-session",
            models=SessionModelState(
                current_model_id="default-model",
                available_models=[ModelInfo(model_id="fixture-model", name="Fixture model")],
            ),
        )

    async def set_session_model(self, model_id, session_id, **_kwargs):
        assert session_id == "fixture-session"
        self.selected = model_id
        return SetSessionModelResponse()

    async def prompt(self, prompt, session_id, **_kwargs):
        assert session_id == "fixture-session"
        Path(os.environ["POWERCONTEXT_ACP_FIXTURE_RESULT"]).write_text(
            json.dumps({"selected_model": self.selected, "prompt": [block.text for block in prompt]}),
            encoding="utf-8",
        )
        return PromptResponse(stop_reason="end_turn")


if __name__ == "__main__":
    asyncio.run(run_agent(ModelAgent(), use_unstable_protocol=True))
