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

"""Read-only admission for final model bytes and exact retained context.

The caller owns rendering, execution, and delivery. Approval never proves that
an external executor used these bytes, and it never authorizes model spending.
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Annotated, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator

from powercontext.builtin.inference.model_payload import parse_model_json
from powercontext.builtin.inference.privacy import (
    InferenceProtectionConfig,
    PrivacyCoverage,
    PrivacyPolicy,
)
from powercontext.builtin.runtime.context_bindings import BindingCodec, ContextBindingError
from powercontext.builtin.runtime.context_reuse import ContextReuseError, ValidatedContextReuse
from powercontext.builtin.runtime.errors import InvalidRuntimeRequestError
from powercontext.errors import PowerContextError

SEND_VALIDITY = timedelta(seconds=60)
RESPONSE_CORRELATION = timedelta(seconds=300)
MAX_CHECK_BODY_BYTES = 1_048_576
MAX_PREPARED_CONTEXTS = 16
_SEND_PURPOSE = "context-check.before_send"
_RELEASE_PURPOSE = "context-check.before_release"
_MARKERS = ("BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1", "BEGIN_POWERCONTEXT_PREPARED_TEXT_V1")
_ALLOWED_HEADERS = frozenset({"content-type", "anthropic-version", "anthropic-beta"})
_MESSAGE_LOCATION = re.compile(r"^/(messages|input)/(0|[1-9][0-9]*)/content(?:/(0|[1-9][0-9]*)/text)?$")


class ContextCheckTooLargeError(PowerContextError):
    """The complete payload cannot be inspected within the declared limit."""

    def __init__(self) -> None:
        super().__init__("context check payload exceeds its byte limit")


class _CheckModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True, hide_input_in_errors=True)


class ModelJsonPayload(_CheckModel):
    schema_version: Literal["powercontext.model-json-payload.v1"] = Field(
        default="powercontext.model-json-payload.v1", alias="schema"
    )
    headers: dict[str, str] = Field(min_length=1, max_length=16)
    body: str = Field(min_length=2, max_length=MAX_CHECK_BODY_BYTES, strict=True)

    @field_validator("headers")
    @classmethod
    def relevant_headers(cls, headers: dict[str, str]) -> dict[str, str]:
        normalized = {name.lower(): value for name, value in headers.items()}
        if (
            len(normalized) != len(headers)
            or not normalized.keys() <= _ALLOWED_HEADERS
            or "content-type" not in normalized
            or any(len(value) > 1024 or "\r" in value or "\n" in value for value in normalized.values())
        ):
            raise ValueError("only unique content and protocol headers are supported")  # noqa: TRY003
        return normalized


class ModelJsonTextLocation(_CheckModel):
    schema_version: Literal["powercontext.model-json-text-location.v1"] = Field(
        default="powercontext.model-json-text-location.v1", alias="schema"
    )
    pointer: str = Field(min_length=1, max_length=256, strict=True)
    start_byte: int = Field(ge=0, strict=True)
    end_byte: int = Field(ge=1, strict=True)


class PreparedContextLocation(_CheckModel):
    binding: str = Field(min_length=1, max_length=65536, strict=True)
    location: ModelJsonTextLocation


class CheckContextBeforeSend(_CheckModel):
    phase: Literal["before_send"]
    target_profile: str = Field(min_length=1, max_length=128, strict=True)
    payload: ModelJsonPayload
    prepared_contexts: Annotated[
        tuple[PreparedContextLocation, ...], Field(max_length=MAX_PREPARED_CONTEXTS, strict=False)
    ]


class CheckContextBeforeRelease(_CheckModel):
    phase: Literal["before_release"]
    target_profile: str = Field(min_length=1, max_length=128, strict=True)
    payload: ModelJsonPayload
    request_binding: str = Field(min_length=1, max_length=65536, strict=True)
    completion: Literal["complete"]


CheckContextRequest: TypeAlias = Annotated[
    CheckContextBeforeSend | CheckContextBeforeRelease, Field(discriminator="phase")
]
CHECK_CONTEXT_REQUEST = TypeAdapter(CheckContextRequest)


class ContextCheckResult(_CheckModel):
    schema_version: Literal["powercontext.context-check.v1"] = Field(
        default="powercontext.context-check.v1", alias="schema"
    )
    phase: Literal["before_send", "before_release"]
    decision: Literal["allow", "block", "withhold"]
    reason_codes: tuple[str, ...] = ()
    coverage: PrivacyCoverage
    policy_version: str
    valid_until: datetime
    correlate_until: datetime | None = None
    binding: str | None = None


def _fingerprint(config: InferenceProtectionConfig) -> str:
    # Include secret rule changes without ever putting rule values in metadata.
    value = config.model_dump(mode="json")
    value["forbidden_values"] = [item.get_secret_value() for item in config.forbidden_values]
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _payload_digest(payload: ModelJsonPayload) -> str:
    return sha256(
        json.dumps(sorted(payload.headers.items()), separators=(",", ":")).encode()
        + b"\x00"
        + payload.body.encode("utf-8")
    ).hexdigest()


def _withheld(
    request: CheckContextRequest, config: InferenceProtectionConfig, now: datetime, reason: str
) -> ContextCheckResult:
    return ContextCheckResult(
        phase=request.phase,
        decision="withhold",
        reason_codes=(reason,),
        coverage=PrivacyCoverage(complete=False),
        policy_version=config.policy_version,
        valid_until=now,
    )


def _message_text(
    body: dict[str, object], location: ModelJsonTextLocation, protocol: str
) -> tuple[str, tuple[int, int, int]]:
    match = _MESSAGE_LOCATION.fullmatch(location.pointer)
    if match is None:
        raise InvalidRuntimeRequestError("context-location-unsupported")
    collection, message_index, content_index = match.groups()
    expected = "input" if protocol == "openai-responses" else "messages"
    if collection != expected or protocol not in {"openai-chat", "openai-responses", "anthropic-messages"}:
        raise InvalidRuntimeRequestError("context-location-unsupported")
    messages = body.get(collection)
    index = int(message_index)
    if not isinstance(messages, list) or index >= len(messages):
        raise InvalidRuntimeRequestError("context-location-invalid")
    message = messages[index]
    if not isinstance(message, dict) or message.get("role") != "user":
        raise InvalidRuntimeRequestError("context-location-trust")
    text = message.get("content")
    part_index = 0 if content_index is None else int(content_index)
    if content_index is not None:
        if not isinstance(text, list) or part_index >= len(text):
            raise InvalidRuntimeRequestError("context-location-invalid")
        part = text[part_index]
        expected_type = "input_text" if protocol == "openai-responses" else "text"
        if not isinstance(part, dict) or part.get("type") != expected_type:
            raise InvalidRuntimeRequestError("context-location-trust")
        text = part.get("text")
    elif protocol != "openai-chat" and protocol != "anthropic-messages":
        raise InvalidRuntimeRequestError("context-location-unsupported")
    if not isinstance(text, str):
        raise InvalidRuntimeRequestError("context-location-invalid")
    return text, (index, part_index, location.start_byte)


def _located_bytes(text: str, location: ModelJsonTextLocation) -> bytes:
    content = text.encode("utf-8")
    start, end = location.start_byte, location.end_byte
    if not 0 <= start < end <= len(content):
        raise InvalidRuntimeRequestError("context-location-invalid")
    try:
        content[:start].decode("utf-8")
        content[start:end].decode("utf-8")
    except UnicodeDecodeError:
        raise InvalidRuntimeRequestError("context-location-character-boundary") from None
    return content[start:end]


def _require_unsplit_markers(parts: Sequence[object]) -> None:
    # A locator covers one JSON string. A marker crossing native text blocks
    # cannot be covered by any supported locator, including in system content.
    overlap = max(map(len, _MARKERS)) - 1
    tail = ""
    for part in parts:
        text = part if isinstance(part, str) else None
        if isinstance(part, dict) and part.get("type") in ("text", "input_text", "output_text"):
            text = part.get("text")
        if not isinstance(text, str):
            tail = ""
            continue
        boundary = len(tail)
        joined = tail + text[:overlap]
        for marker in _MARKERS:
            start = joined.find(marker, max(0, boundary - len(marker) + 1))
            if 0 <= start < boundary:
                raise InvalidRuntimeRequestError("context-binding-required")
        tail = (tail + text[-overlap:])[-overlap:]


def _require_bound_markers(value: object, locations: Mapping[str, list[tuple[int, int]]], pointer: str = "") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                raise InvalidRuntimeRequestError("context-payload-json")
            escaped = key.replace("~", "~0").replace("/", "~1")
            _require_bound_markers(child, locations, f"{pointer}/{escaped}")
    elif isinstance(value, list):
        _require_unsplit_markers(value)
        for index, child in enumerate(value):
            _require_bound_markers(child, locations, f"{pointer}/{index}")
    elif isinstance(value, str):
        for marker in _MARKERS:
            start = value.find(marker)
            while start >= 0:
                offset = len(value[:start].encode("utf-8"))
                if not any(left <= offset < right for left, right in locations.get(pointer, ())):
                    raise InvalidRuntimeRequestError("context-binding-required")
                start = value.find(marker, start + len(marker))


async def _validate_prepared(
    request: CheckContextBeforeSend,
    config: InferenceProtectionConfig,
    validate_reuse: Callable[[str], Awaitable[ValidatedContextReuse]],
) -> datetime | None:
    profile = config.targets[request.target_profile]
    if profile.require_prepared_contexts and not request.prepared_contexts:
        raise InvalidRuntimeRequestError("context-binding-required")
    body = parse_model_json(request.payload.body.encode("utf-8"), max_depth=config.max_json_depth)
    previous: tuple[int, int, int] | None = None
    locations: dict[str, list[tuple[int, int]]] = {}
    expiry: datetime | None = None
    for item in request.prepared_contexts:
        text, order = _message_text(body, item.location, profile.protocol)
        if previous is not None and order <= previous:
            raise InvalidRuntimeRequestError("context-location-order")
        previous = order
        bounds = locations.setdefault(item.location.pointer, [])
        if bounds and item.location.start_byte < bounds[-1][1]:
            raise InvalidRuntimeRequestError("context-location-overlap")
        actual = _located_bytes(text, item.location)
        verified = await validate_reuse(item.binding)
        if len(actual) != verified.content_bytes or sha256(actual).hexdigest() != verified.content_digest:
            raise InvalidRuntimeRequestError("context-location-content")
        if verified.trust != "historical-data":
            raise InvalidRuntimeRequestError("context-location-trust")
        bounds.append((item.location.start_byte, item.location.end_byte))
        expiry = verified.expires_at if expiry is None else min(expiry, verified.expires_at)
    _require_bound_markers(body, locations)
    return expiry


async def _correlate(
    request: CheckContextBeforeRelease,
    *,
    codec: BindingCodec,
    scope_id: str,
    binding_identity: str,
    fingerprint: str,
    now: datetime,
    validate_reuse: Callable[[str], Awaitable[ValidatedContextReuse]],
) -> None:
    granted = codec.open(request.request_binding, purpose=_SEND_PURPOSE, now=now)
    if (
        granted.get("identity") != binding_identity
        or granted.get("scope_id") != scope_id
        or granted.get("target_profile") != request.target_profile
        or granted.get("policy") != fingerprint
        or granted.get("phase") != "before_send"
    ):
        raise ContextBindingError("context-binding-invalid")
    retained = granted.get("prepared_bindings")
    if not isinstance(retained, list) or len(retained) > MAX_PREPARED_CONTEXTS:
        raise ContextBindingError("context-binding-invalid")
    for binding in retained:
        if not isinstance(binding, str):
            raise ContextBindingError("context-binding-invalid")
        await validate_reuse(binding)


async def check_context(
    request: CheckContextRequest,
    *,
    scope_id: str,
    config: InferenceProtectionConfig,
    codec: BindingCodec,
    binding_identity: str,
    now: datetime,
    validate_reuse: Callable[[str], Awaitable[ValidatedContextReuse]],
) -> ContextCheckResult:
    """Check complete final bytes under current authorization supplied by the owner."""
    now = now.astimezone(UTC)
    try:
        body = request.payload.body.encode("utf-8")
    except UnicodeEncodeError:
        raise InvalidRuntimeRequestError("context-payload-encoding") from None
    limit = config.max_payload_bytes if request.phase == "before_send" else config.max_response_bytes
    if len(body) > min(limit, MAX_CHECK_BODY_BYTES):
        raise ContextCheckTooLargeError
    if config.mode != "enforcing":
        return _withheld(request, config, now, "enforcement-not-configured")
    policy = PrivacyPolicy(config)
    assessed = policy.inspect_json(
        body, phase=request.phase, target_profile=request.target_profile, headers=request.payload.headers
    )
    if assessed.decision != "allow" or not assessed.coverage.complete:
        return ContextCheckResult(
            phase=request.phase,
            decision=assessed.decision if assessed.decision != "allow" else "withhold",
            reason_codes=assessed.reason_codes,
            coverage=assessed.coverage,
            policy_version=assessed.policy_version,
            valid_until=now,
        )
    fingerprint = _fingerprint(config)
    expiry: datetime | None = None
    try:
        if isinstance(request, CheckContextBeforeSend):
            expiry = await _validate_prepared(request, config, validate_reuse)
        else:
            await _correlate(
                request,
                codec=codec,
                scope_id=scope_id,
                binding_identity=binding_identity,
                fingerprint=fingerprint,
                now=now,
                validate_reuse=validate_reuse,
            )
    except (ContextBindingError, ContextReuseError):
        return _withheld(request, config, now, "context-refresh-required")
    deadline = now + SEND_VALIDITY
    if expiry is not None:
        deadline = min(deadline, expiry)
    correlate_until = now + RESPONSE_CORRELATION if request.phase == "before_send" else None
    granted: dict[str, object] = {
        "phase": request.phase,
        "identity": binding_identity,
        "scope_id": scope_id,
        "target_profile": request.target_profile,
        "payload_digest": _payload_digest(request.payload),
        "policy": fingerprint,
        "valid_until": deadline.isoformat(),
    }
    if isinstance(request, CheckContextBeforeSend):
        granted["prepared_bindings"] = [item.binding for item in request.prepared_contexts]
    else:
        granted["request_digest"] = sha256(request.request_binding.encode()).hexdigest()
    try:
        binding = codec.seal(
            granted,
            purpose=_SEND_PURPOSE if request.phase == "before_send" else _RELEASE_PURPOSE,
            expires_at=correlate_until or deadline,
        )
    except ContextBindingError:
        return _withheld(request, config, now, "binding-capacity-exceeded")
    return ContextCheckResult(
        phase=request.phase,
        decision="allow",
        coverage=assessed.coverage,
        policy_version=config.policy_version,
        valid_until=deadline,
        correlate_until=correlate_until,
        binding=binding,
    )
