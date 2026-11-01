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

"""Generate one deterministic report for each exact ticket snapshot."""

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

    title_prefix: str = "Incident report"
    fail_on_text: str | None = None


class IncidentReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    title: str = Field(min_length=1)
    evidence_text: str = Field(min_length=1)


families = (
    ArtifactFamily(
        family="incident-report",
        schema_version="1",
        content_model=IncidentReport,
        search=ArtifactSearchDeclaration(
            version="1",
            indexes=(TextIndex(name="lexical", projection_version="1", fields=("title", "evidence_text")),),
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


class IncidentReportPipeline:
    def __init__(self, config: Config) -> None:
        self.config = config

    async def generate(self, batch: GenerationBatch) -> list[ArtifactDraft]:
        drafts = []
        for source in batch.sources:
            evidence = TextEvidence.model_validate(source.value)
            if self.config.fail_on_text and self.config.fail_on_text in evidence.content:
                raise ValueError("Demo generation failure: no drafts from this window will be committed")  # noqa: TRY003
            identity = json.dumps(source.ref.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
            drafts.append(
                ArtifactDraft(
                    family="incident-report",
                    schema_version="1",
                    artifact_id=hashlib.sha256(identity.encode()).hexdigest(),
                    content=IncidentReport(
                        title=f"{self.config.title_prefix}: {source.ref.source_id}",
                        evidence_text=evidence.content,
                    ).model_dump(mode="json"),
                    source_refs=(source.ref,),
                )
            )
        return drafts


async def create_pipeline(ctx: GenerationContext, config: Config) -> IncidentReportPipeline:
    return IncidentReportPipeline(config)
