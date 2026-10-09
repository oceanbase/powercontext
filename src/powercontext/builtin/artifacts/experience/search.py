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

"""Search projections owned by the Experience Artifact Family."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

from powercontext.artifacts import ArtifactRef
from powercontext.artifacts.search import ArtifactSearchMatch, ArtifactSearchQuery, ChannelScore
from powercontext.builtin.artifacts.experience.models import Experience, ExperienceContent
from powercontext.builtin.artifacts.search import AdmissionCounts, EmptyFilters, LexicalSearchAdmission, analyze_text


class ExperienceSearchRequest(ArtifactSearchQuery):
    """Public text search controls for approved, active Experience heads."""

    mode: Literal["text"] | None = None
    filters: EmptyFilters = Field(default_factory=EmptyFilters)
    admission: LexicalSearchAdmission = Field(default_factory=LexicalSearchAdmission)
    min_score: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)


@dataclass(frozen=True)
class ExperienceSearchOutcome:
    """Experience hits plus the admission accounting for the search that produced them.

    It replaces the bare ``tuple[ExperienceSearchHit, ...]`` the ``experience_recall`` callback
    used to return, because the Runtime's recall gate needs the retrieved/admitted pair and the
    callback signature is part of the Runtime's construction surface.

    ``admission`` is ``None`` when the search did not measure — for example a configured index
    with nothing to report — rather than a fabricated ``(0, 0)``. The counters are aggregates
    with no hit identity, so carrying them costs nothing the callback did not already compute.
    """

    hits: tuple[ExperienceSearchHit, ...] = ()
    admission: AdmissionCounts | None = None
    matches: tuple[ArtifactSearchMatch, ...] = ()
    artifacts: tuple[Experience, ...] | None = None


class ExperienceSearchHit(BaseModel):
    """One relevant approved Experience head."""

    artifact_ref: ArtifactRef
    content: ExperienceContent
    retrieval_score: float | None = None
    channel_scores: dict[str, ChannelScore] | None = None


def render_experience(content: ExperienceContent, /) -> str:
    """Render complete typed Experience content for bounded context delivery."""

    lines = [
        f"Situation: {content.situation}",
        f"Action: {content.action}",
        f"Outcome: {content.outcome}",
        f"Lesson: {content.lesson}",
    ]
    if content.failure is not None:
        lines.append(f"Failure cue: {content.failure.signature.recall_cue}")
        if content.failure.signature.symptom is not None:
            lines.append(f"Symptom: {content.failure.signature.symptom}")
        lines.append(f"Repair surface: {content.failure.repair_surface}")
        lines.append(f"Verification condition: {content.failure.verification.condition}")
        lines.append(f"Verification check: {content.failure.verification.check_subject}")
    return "\n".join(lines)


def experience_searchable_text(content: ExperienceContent, /) -> str:
    """Build the deterministic lexical projection for one Experience Revision."""

    return analyze_text(experience_search_text(content))


def experience_search_text(content: ExperienceContent, /) -> str:
    """Return only user-authored fields so renderer labels cannot cause matches."""

    fields = [content.situation, content.action, content.outcome, content.lesson]
    if content.failure is not None:
        fields.append(content.failure.signature.recall_cue)
        if content.failure.signature.symptom is not None:
            fields.append(content.failure.signature.symptom)
    return "\n".join(fields)


__all__ = [
    "ExperienceSearchHit",
    "ExperienceSearchOutcome",
    "ExperienceSearchRequest",
    "experience_search_text",
    "experience_searchable_text",
    "render_experience",
]
