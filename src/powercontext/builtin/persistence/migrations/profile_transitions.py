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

"""Accepted managed embedding-profile switches recorded beside release evidence."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, model_validator

from powercontext.builtin.artifacts.memory import EmbeddingProfile

from .models import MigrationError


def verify_release_checksums(recorded: Mapping[str, str], checksums: Mapping[str, str]) -> None:
    """Require every recorded released revision to still match its resources."""
    for revision, checksum in recorded.items():
        if checksums.get(revision) != checksum:
            raise MigrationError("checksum_conflict", "A recorded released migration resource changed.")


class EmbeddingProfileTransition(BaseModel):
    """One operator-accepted switch of the managed shared embedding profile."""

    model_config = ConfigDict(extra="forbid")

    previous: EmbeddingProfile
    target: EmbeddingProfile
    state: Literal["pending", "complete"]

    @model_validator(mode="after")
    def changes_profile(self) -> Self:
        if self.previous == self.target:
            raise ValueError("An embedding profile transition must change the profile")  # noqa: TRY003
        return self


def read_embedding_profile_transitions(value: Any) -> list[EmbeddingProfileTransition]:
    return [EmbeddingProfileTransition.model_validate(item) for item in value or ()]


def validate_embedding_profile_transitions(transitions: Iterable[EmbeddingProfileTransition]) -> None:
    """Require one ordered switch chain whose only unfinished step is the last."""
    ordered = list(transitions)
    for index, transition in enumerate(ordered):
        if index and ordered[index - 1].target != transition.previous:
            raise ValueError("Embedding profile transitions must form one chain")  # noqa: TRY003
        if transition.state == "pending" and index != len(ordered) - 1:
            raise ValueError("Only the latest embedding profile transition may be pending")  # noqa: TRY003


def accept_embedding_profile_switch(
    transitions: Iterable[EmbeddingProfileTransition], previous: EmbeddingProfile, target: EmbeddingProfile
) -> list[EmbeddingProfileTransition]:
    """Return the chain that records accepting a switch from the stored profile.

    A pending switch whose target is already the stored profile finished its
    storage activation before its completion was journaled, so accepting the
    next switch first closes it.
    """

    chain = list(transitions)
    if chain and chain[-1].state == "pending":
        pending = chain[-1]
        if pending.target == previous:
            chain[-1] = pending.model_copy(update={"state": "complete"})
        elif pending.previous != previous or pending.target != target:
            raise MigrationError(
                "recovery_required", "Another embedding profile switch is pending; finish or revert it first."
            )
    if previous != target and not (chain and chain[-1].state == "pending"):
        chain.append(EmbeddingProfileTransition(previous=previous, target=target, state="pending"))
    return chain


def finish_embedding_profile_transition(
    transitions: Iterable[EmbeddingProfileTransition],
) -> list[EmbeddingProfileTransition] | None:
    """Return the chain with its pending switch completed, or None when none is pending."""

    chain = list(transitions)
    if not chain or chain[-1].state != "pending":
        return None
    chain[-1] = chain[-1].model_copy(update={"state": "complete"})
    return chain


def verify_embedding_profile_evidence(
    evidence: Mapping[str, Any],
    *,
    profile: EmbeddingProfile | None,
    checksums: Mapping[str, str],
    checksums_for: Callable[[EmbeddingProfile], Mapping[str, str]],
) -> None:
    """Verify the recorded releases without rewriting them for a profile switch.

    A completed window records the checksums of the bundle it ran. Switching the
    managed embedding profile changes every profile-bound revision checksum, so
    the map stays untouched and the accepted switch is recorded as an ordered
    transition instead. Rebuilding the checksums of the bundle that wrote the map
    proves that the only difference is the declared profile, and a pending
    transition still admits storage whose activation committed before the journal
    was updated.
    """

    transitions = read_embedding_profile_transitions(evidence.get("embedding_profile_transitions"))
    completed = [item for item in transitions if item.state == "complete"]
    pending = transitions[-1] if transitions and transitions[-1].state == "pending" else None
    chain = completed
    if pending is not None:
        if pending.target == profile:
            # Activation may have committed before this journal recorded it.
            chain = [*completed, pending]
        elif pending.previous != profile:
            raise MigrationError(
                "embedding_profile_mismatch",
                "The pending embedding profile switch does not start from the configured profile.",
            )
    if not chain:
        verify_release_checksums(evidence["release_checksums"], checksums)
        return
    if chain[-1].target != profile:
        raise MigrationError(
            "embedding_profile_mismatch",
            "The recorded embedding profile switches do not reach the configured profile.",
        )
    verify_release_checksums(evidence["release_checksums"], checksums_for(chain[0].previous))


__all__ = [
    "EmbeddingProfileTransition",
    "accept_embedding_profile_switch",
    "finish_embedding_profile_transition",
    "read_embedding_profile_transitions",
    "validate_embedding_profile_transitions",
    "verify_embedding_profile_evidence",
    "verify_release_checksums",
]
