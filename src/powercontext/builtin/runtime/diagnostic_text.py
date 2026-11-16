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

"""Exact already-read candidate text for the opt-in diagnostic store only."""

from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from powercontext.builtin.artifacts.experience.search import render_experience

if TYPE_CHECKING:
    from powercontext.builtin.artifacts.experience import ExperienceSearchHit
    from powercontext.builtin.artifacts.profile.models import Profile
    from powercontext.builtin.artifacts.topic_memory.models import TopicMemorySearchHit
    from powercontext.builtin.runtime.atomic_memory import AtomicMemorySearchHit
    from powercontext.builtin.runtime.prepared_code import PreparedCodeCandidate


def memory_text(hit: AtomicMemorySearchHit) -> dict[str, Any]:
    return {"artifact": hit.hit.artifact_ref.model_dump(mode="json"), "text": hit.text}


def experience_text(hit: ExperienceSearchHit) -> dict[str, Any]:
    return {"artifact": hit.artifact_ref.model_dump(mode="json"), "text": render_experience(hit.content)}


def topic_text(hit: TopicMemorySearchHit) -> dict[str, Any]:
    return {
        "artifact": hit.artifact_ref.model_dump(mode="json"),
        # A Topic search returns progressively disclosed fields, not the full
        # Artifact body. Keep exactly these fields; never fetch more for capture.
        "text": "\n\n".join(part for part in (hit.title, hit.summary, hit.snippet) if part is not None),
    }


def profile_text(profile: Profile) -> dict[str, Any]:
    return {"artifact": profile.as_ref().model_dump(mode="json"), "text": profile.content.content}


def code_text(candidate: PreparedCodeCandidate) -> dict[str, Any]:
    return {"code_evidence": asdict(candidate.origin), "text": candidate.content}
