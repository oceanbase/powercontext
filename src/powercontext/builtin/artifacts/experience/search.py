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

from pydantic import BaseModel

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.experience.models import ExperienceContent
from powercontext.builtin.artifacts.search import analyze_text


class ExperienceSearchHit(BaseModel):
    """One relevant approved Experience head."""

    artifact_ref: ArtifactRef
    content: ExperienceContent


def render_experience(content: ExperienceContent, /) -> str:
    """Render complete typed Experience content for bounded context delivery."""

    return "\n".join((
        f"Situation: {content.situation}",
        f"Action: {content.action}",
        f"Outcome: {content.outcome}",
        f"Lesson: {content.lesson}",
    ))


def experience_searchable_text(content: ExperienceContent, /) -> str:
    """Build the deterministic lexical projection for one Experience Revision."""

    return analyze_text(experience_search_text(content))


def experience_search_text(content: ExperienceContent, /) -> str:
    """Return only user-authored fields so renderer labels cannot cause matches."""

    return "\n".join((content.situation, content.action, content.outcome, content.lesson))


__all__ = ["ExperienceSearchHit", "experience_search_text", "experience_searchable_text", "render_experience"]
