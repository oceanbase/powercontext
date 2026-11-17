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

"""Standing sharing agreement: the rule, who accepted it, and what one pin fixed.

Three things are deliberately distinct and never derived from each other:

- ``terms_digest`` binds the agreement (use, audience, retention, selector).
- ``membership_digest`` binds the structure the selector resolved to.
- each pinned input's own ``digest`` binds the exact material observed.

An agreement is not a grant: it says what may be used, and current Access still decides
whether a given principal may read the origin or write the target.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.scope.models import ScopeSelection
from powercontext.sources import SourceRef

MAX_SHARE_ID_LENGTH = 64
MAX_SHARE_LABEL_LENGTH = 128
MAX_SHARE_ACTOR_LENGTH = 200
MAX_SHARE_NOTIFICATION_KEY_LENGTH = 128

ShareSide = Literal["origin", "audience"]
ShareRetention = Literal["reference", "independent"]
ShareInputKind = Literal["source", "artifact"]
ShareRuleStatus = Literal["active", "invalidated"]


class ShareError(Exception):
    """Base failure for the standing sharing agreement."""

    code = "share_error"


class ShareRuleNotFoundError(ShareError, LookupError):
    code = "share_rule_not_found"


class ShareRuleVersionConflictError(ShareError, RuntimeError):
    code = "share_rule_version_conflict"

    def __init__(self, rule_id: str, expected: int, current: int) -> None:
        self.rule_id = rule_id
        self.expected = expected
        self.current = current
        super().__init__("share_rule_version_conflict")


class ShareTermsChangedError(ShareError, RuntimeError):
    """The agreement changed after a snapshot pinned it."""

    code = "shared_use_terms"


class ShareRejectedError(ShareError, ValueError):
    code = "shared_use_rejected"


class ShareAcceptance(BaseModel):
    """One authenticated actor's acceptance of exactly the Scopes it claims."""

    model_config = ConfigDict(frozen=True)

    acceptance_id: str
    actor: str
    side: ShareSide
    covered_scope_ids: tuple[str, ...]
    terms_digest: str
    accepted_at: datetime


class ShareRuleTerms(BaseModel):
    """The labels an agreement binds, for the authority check that enforces them."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    use: str = Field(min_length=1, max_length=MAX_SHARE_LABEL_LENGTH)
    audience: str = Field(min_length=1, max_length=MAX_SHARE_LABEL_LENGTH)
    retention: ShareRetention = "reference"

    @model_validator(mode="after")
    def reject_blank(self):
        for value in (self.use, self.audience):
            if not value.strip() or value != value.strip():
                raise ValueError("share terms labels must be non-empty and trimmed")  # noqa: TRY003
        return self


class ShareRuleDraft(BaseModel):
    """One complete rule version, replacing the previous one atomically."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    selection: ScopeSelection
    terms: ShareRuleTerms

    def terms_digest(self) -> str:
        """Bind the agreement itself: the selector and its terms, not any result."""

        payload = json.dumps(
            {"selection": self.selection.model_dump(mode="json"), "terms": self.terms.model_dump(mode="json")},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()


class ShareRule(BaseModel):
    """One immutable rule version with its current head status."""

    model_config = ConfigDict(frozen=True)

    scope_id: str
    rule_id: str
    version: int = Field(ge=1)
    status: ShareRuleStatus
    selection: ScopeSelection
    terms: ShareRuleTerms
    terms_digest: str
    created_at: datetime
    acceptances: tuple[ShareAcceptance, ...] = ()

    def accepted_scope_ids(self, side: ShareSide) -> tuple[str, ...]:
        covered: set[str] = set()
        for acceptance in self.acceptances:
            if acceptance.side == side:
                covered.update(acceptance.covered_scope_ids)
        return tuple(sorted(covered))


class ShareInput(BaseModel):
    """One exact input a pin fixed, with the Scope that owns it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: ShareInputKind
    origin_scope_id: str
    digest: str
    source: SourceRef | None = None
    artifact: ArtifactRef | None = None

    @model_validator(mode="after")
    def require_one_reference(self):
        if (self.source is None) == (self.artifact is None):
            raise ValueError("a pinned input names exactly one Source or Artifact")  # noqa: TRY003
        if self.kind == "source" and self.source is None:
            raise ValueError("a Source input requires a Source reference")  # noqa: TRY003
        if self.kind == "artifact" and self.artifact is None:
            raise ValueError("an Artifact input requires an Artifact reference")  # noqa: TRY003
        if not self.digest.startswith("sha256:") or len(self.digest) != 71:
            raise ValueError("a pinned input requires a sha256 digest")  # noqa: TRY003
        return self

    @property
    def reference(self) -> SourceRef | ArtifactRef:
        reference = self.source if self.source is not None else self.artifact
        if reference is None:
            raise ValueError("pinned input has no reference")  # noqa: TRY003
        return reference

    def key(self) -> tuple[str, str, str]:
        return (self.origin_scope_id, self.kind, self.reference.model_dump_json())


class ShareSnapshotDraft(BaseModel):
    """The immutable inputs one evaluation pinned, before any execution claims them."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    rule_id: str
    rule_version: int = Field(ge=1)
    terms_digest: str
    membership_digest: str
    inputs: tuple[ShareInput, ...] = Field(min_length=1)
    notification_key: str | None = Field(default=None, max_length=MAX_SHARE_NOTIFICATION_KEY_LENGTH)

    @field_validator("terms_digest", "membership_digest")
    @classmethod
    def validate_digest(cls, value: str) -> str:
        if not value.startswith("sha256:") or len(value) != 71:
            raise ValueError("snapshot digests must be sha256")  # noqa: TRY003
        return value

    @model_validator(mode="after")
    def reject_duplicate_inputs(self):
        keys = [item.key() for item in self.inputs]
        if len(set(keys)) != len(keys):
            raise ValueError("a pinned input is recorded twice")  # noqa: TRY003
        return self

    def inputs_digest(self) -> str:
        """Bind exactly what was pinned, independent of the terms and the structure."""

        payload = json.dumps(
            sorted(
                [item.origin_scope_id, item.kind, item.reference.model_dump(mode="json"), item.digest]
                for item in self.inputs
            ),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()


class ShareSnapshot(BaseModel):
    """One immutable pin. It becomes claimable by exactly one Dream execution."""

    model_config = ConfigDict(frozen=True)

    scope_id: str
    snapshot_id: str
    rule_id: str
    rule_version: int
    terms_digest: str
    membership_digest: str
    inputs_digest: str
    inputs: tuple[ShareInput, ...]
    notification_key: str | None = None
    dream_run_id: str | None = None
    created_at: datetime

    @property
    def claimed(self) -> bool:
        return self.dream_run_id is not None


def membership_digest(scope_versions: tuple[tuple[str, int], ...]) -> str:
    """Bind the structure a selector resolved to, as ids with their versions."""

    payload = json.dumps(sorted(scope_versions), separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()


__all__ = [
    "MAX_SHARE_ACTOR_LENGTH",
    "MAX_SHARE_ID_LENGTH",
    "ShareAcceptance",
    "ShareError",
    "ShareInput",
    "ShareRejectedError",
    "ShareRetention",
    "ShareRule",
    "ShareRuleDraft",
    "ShareRuleNotFoundError",
    "ShareRuleTerms",
    "ShareRuleVersionConflictError",
    "ShareSide",
    "ShareSnapshot",
    "ShareSnapshotDraft",
    "ShareTermsChangedError",
    "membership_digest",
]
