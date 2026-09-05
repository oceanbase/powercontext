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

"""Authenticated Handoff generation origin across independent HTTP requests."""

from __future__ import annotations

import base64
import binascii
import hmac
import json
from hashlib import sha256
from typing import Annotated, Literal

import rfc8785
from pydantic import BaseModel, ConfigDict, Field, model_validator

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.prompt import PromptError, ResolvedPrompt

_PURPOSE = "powercontext.handoff-generation.v1"
_Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
_Version = Annotated[str, Field(min_length=1, max_length=256)]


class HandoffGenerationEnvelope(BaseModel):
    """Transient receipt. Never stored in an Artifact or treated as additional authority."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    receipt: Annotated[str, Field(min_length=1, max_length=8192, repr=False)]


class HandoffGenerationOrigin(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    scope_id: Annotated[str, Field(min_length=1, max_length=256)]
    prompt_key: Literal["handoff.generate"]
    selection: Literal["built_in", "artifact"]
    artifact: ArtifactRef | None
    definition_version: _Version
    builtin_version: _Version
    compiled_digest: _Digest
    original_draft_digest: _Digest

    @model_validator(mode="after")
    def require_matching_selection(self) -> HandoffGenerationOrigin:
        if self.selection == "built_in":
            if self.artifact is not None:
                raise ValueError("Built-in generation cannot have an Artifact reference")  # noqa: TRY003
        elif self.artifact is None or self.artifact.family != "prompt" or self.artifact.artifact_id != self.prompt_key:
            raise ValueError("Custom generation requires its exact operational Prompt")  # noqa: TRY003
        return self


class HandoffGenerationMetadata(HandoffGenerationOrigin):
    """Server-derived origin plus whether the final editable content differs from the generated draft."""

    edit_status: Literal["unchanged", "edited"]


def handoff_draft_digest(value: BaseModel) -> str:
    """Hash editable fields equally for a Draft and Content, excluding receipts and attribution."""

    editable = value.model_dump(mode="json", by_alias=True, exclude={"generation", "schema_version"})
    return sha256(rfc8785.dumps(editable)).hexdigest()


class HandoffGenerationReceipts:
    """Sign with the current key and verify with retained keys during explicit key rotation."""

    def __init__(self, signing_key: bytes, *, verification_keys: tuple[bytes, ...] = ()) -> None:
        keys = (signing_key, *verification_keys)
        if len(keys) > 9 or any(len(key) < 32 for key in keys):
            raise ValueError("Generation signing keys must contain at least 32 bytes; retain at most eight old keys")  # noqa: TRY003
        self._keys = tuple(hmac.digest(key, _PURPOSE.encode(), "sha256") for key in keys)

    def issue(self, scope_id: str, selection: ResolvedPrompt, draft: BaseModel) -> HandoffGenerationEnvelope:
        if selection.scope_id != scope_id or selection.key != "handoff.generate":
            raise PromptError("invalid_handoff_generation")
        origin = HandoffGenerationOrigin(
            scope_id=scope_id,
            prompt_key="handoff.generate",
            selection=selection.selection,
            artifact=selection.artifact,
            definition_version=selection.definition_version,
            builtin_version=selection.builtin_version,
            compiled_digest=selection.compiled_digest,
            original_draft_digest=handoff_draft_digest(draft),
        )
        payload = _encode(rfc8785.dumps({"purpose": _PURPOSE, "origin": origin.model_dump(mode="json")}))
        signature = _encode(hmac.digest(self._keys[0], payload.encode(), "sha256"))
        return HandoffGenerationEnvelope(receipt=f"{payload}.{signature}")

    def verify(
        self, scope_id: str, envelope: HandoffGenerationEnvelope, content: BaseModel
    ) -> HandoffGenerationMetadata:
        try:
            origin = self._verify_origin(scope_id, envelope.receipt)
        except (ValueError, TypeError, UnicodeError, binascii.Error):
            raise PromptError("invalid_handoff_generation") from None
        return HandoffGenerationMetadata(
            **origin.model_dump(mode="python"),
            edit_status="unchanged" if handoff_draft_digest(content) == origin.original_draft_digest else "edited",
        )

    def _verify_origin(self, scope_id: str, receipt: str) -> HandoffGenerationOrigin:
        payload, signature = receipt.split(".")
        supplied = _decode(signature)
        if not any(hmac.compare_digest(hmac.digest(key, payload.encode(), "sha256"), supplied) for key in self._keys):
            raise ValueError("signature")
        decoded = json.loads(_decode(payload))
        if set(decoded) != {"purpose", "origin"} or decoded["purpose"] != _PURPOSE:
            raise ValueError("purpose")
        origin = HandoffGenerationOrigin.model_validate_json(json.dumps(decoded["origin"]))
        if origin.scope_id != scope_id:
            raise ValueError("scope")
        return origin


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value: str) -> bytes:
    decoded = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    if _encode(decoded) != value:
        raise ValueError("non-canonical receipt")  # noqa: TRY003
    return decoded
