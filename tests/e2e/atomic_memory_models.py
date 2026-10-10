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

"""Deterministic inference for complete Atomic extraction and reconciliation."""

from __future__ import annotations

import json

from pydantic_ai.messages import ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models.function import FunctionModel


def independent_atomic_memory_model(text: str, *, kind: str = "decision") -> FunctionModel:
    """Retain a supplied fixture fact with the actual input evidence identities."""

    def respond(messages, _info) -> ModelResponse:
        request = next(
            json.loads(part.content)
            for message in reversed(messages)
            for part in message.parts
            if isinstance(part, UserPromptPart) and isinstance(part.content, str)
        )
        if "proposal" in request:
            proposal = request["proposal"]
            value = {
                "action": "create",
                "compared_ids": [item["item_id"] for item in request["related"]],
                "content": {"kind": proposal["kind"], "text": proposal["text"]},
                "evidence_ids": proposal["evidence_ids"],
                "reason": "Preserve the independent host workflow fact.",
            }
        else:
            value = {
                "candidates": [
                    {
                        "kind": kind,
                        "text": text,
                        "evidence_ids": [request["evidence"][0]["evidence_id"]],
                    }
                ]
            }
        return ModelResponse(parts=[TextPart(json.dumps(value))])

    return FunctionModel(respond)
