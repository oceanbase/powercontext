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

"""Summarize each Source window without an external model dependency."""

import hashlib
import json

from pydantic import BaseModel, ConfigDict, Field

from powercontext.artifact_plugins import (
    ArtifactDraft,
    ArtifactFamily,
    ArtifactSearchDeclaration,
    GenerationBatch,
    GenerationContext,
    InputContract,
    TextIndex,
)
from powercontext.sources import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence

plugin_api_version = "1"


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    language: str = Field(default="简体中文", min_length=1)


class TicketSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    language: str
    source_ids: list[str] = Field(min_length=1)
    summary_text: str = Field(min_length=1)


families = (
    ArtifactFamily(
        family="ticket-summary",
        schema_version="1",
        content_model=TicketSummary,
        search=ArtifactSearchDeclaration(
            version="1",
            indexes=(TextIndex(name="lexical", projection_version="1", fields=("summary_text",)),),
            modes=("text",),
            default_mode="text",
        ),
    ),
)
inputs = InputContract(
    projections={
        "content": TEXT_EVIDENCE_PROJECTION_KEY,
    }
)


class TicketSummaryPipeline:
    def __init__(self, config: Config) -> None:
        self.config = config

    async def generate(self, batch: GenerationBatch) -> list[ArtifactDraft]:
        refs = tuple(source.ref for source in batch.sources)
        identity = json.dumps([ref.model_dump(mode="json") for ref in refs], sort_keys=True, separators=(",", ":"))
        return [
            ArtifactDraft(
                family="ticket-summary",
                schema_version="1",
                artifact_id=hashlib.sha256(identity.encode()).hexdigest(),
                content=TicketSummary(
                    language=self.config.language,
                    source_ids=[ref.source_id for ref in refs],
                    summary_text="\n".join(
                        TextEvidence.model_validate(source.value).content for source in batch.sources
                    ),
                ).model_dump(mode="json"),
                source_refs=refs,
            )
        ]


async def create_pipeline(ctx: GenerationContext, config: Config) -> TicketSummaryPipeline:
    return TicketSummaryPipeline(config)
