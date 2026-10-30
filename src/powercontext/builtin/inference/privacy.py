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

"""Bounded deterministic rules shared by local inference and context checks.

Configuration is trusted deployment policy. Inspection neither calls models nor
rewrites content. The fixed format rules are intentionally narrower than general
PII detection; no arbitrary, potentially unbounded regular expressions execute.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Literal, cast
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from powercontext.builtin.inference.model_payload import (
    PrivacyProtocol,
    complete_response,
    complete_tool_arguments,
    contains_opaque_content,
    joined_content_text,
    json_unsigned_integer,
    nested_json_text,
    parse_json_evidence,
    tool_arguments,
)

PrivacyPhase = Literal["before_send", "before_release"]
PrivacyMode = Literal["disabled", "shadow", "advisory", "enforcing"]


class UpstreamProfile(BaseModel):
    """Trusted exact destination and the carrier actually verified for it."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    endpoint: str = Field(min_length=1, max_length=2048)
    model: str = Field(min_length=1, max_length=256)
    protocol: PrivacyProtocol
    allow_streaming: bool = False
    response_models: tuple[str, ...] = Field(default=(), max_length=16)
    allowed_cache_retention: tuple[Literal["provider-default", "in_memory", "24h", "5m", "1h", "30m"], ...] = ()
    context_window_tokens: int | None = Field(default=None, ge=1)
    max_output_tokens: int | None = Field(default=None, ge=1)
    validated_utf8_token_bound: bool = False
    protocol_token_reserve: int = Field(default=64, ge=0, le=65536)

    @field_validator("endpoint")
    @classmethod
    def exact_endpoint(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"https", "http"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("profile endpoint must be an exact HTTP URL without credentials or query")  # noqa: TRY003
        return value


class InferenceProtectionConfig(BaseModel):
    """Opt-in deployment rules, independent of representation optimization."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    mode: PrivacyMode = "disabled"
    policy_version: str = Field(default="privacy.v1", min_length=1, max_length=128)
    forbidden_values: tuple[SecretStr, ...] = Field(default=(), repr=False, max_length=256)
    forbidden_patterns: tuple[Literal["email", "private_key", "api_token"], ...] = ()
    targets: dict[str, UpstreamProfile] = Field(default_factory=dict, max_length=64)
    max_payload_bytes: int = Field(default=1_048_576, ge=256, le=8_388_608)
    max_response_bytes: int = Field(default=1_048_576, ge=256, le=8_388_608)
    max_json_depth: int = Field(default=64, ge=1, le=128)
    response_timeout_seconds: float = Field(default=60, gt=0, le=600, allow_inf_nan=False)
    max_concurrent_responses: int = Field(default=16, ge=1, le=256)

    @model_validator(mode="after")
    def bounded_secrets(self) -> InferenceProtectionConfig:
        if any(not value.get_secret_value() or len(value.get_secret_value()) > 4096 for value in self.forbidden_values):
            raise ValueError("forbidden values must contain between 1 and 4096 characters")  # noqa: TRY003
        return self


class PrivacyCoverage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    profile: str = "text-and-tools.v1"
    complete: bool


class PrivacyDecision(BaseModel):
    """A content-free rule assessment; the caller owns its enforcement action."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    decision: Literal["allow", "block", "withhold"]
    phase: PrivacyPhase
    reason_codes: tuple[str, ...] = ()
    coverage: PrivacyCoverage
    policy_version: str
    mode: PrivacyMode


_PATTERNS = {
    "email": re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]{1,128}@[A-Za-z0-9-]{1,63}(?:\.[A-Za-z0-9-]{1,63})+"),
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY-----"),
    "api_token": re.compile(r"(?:sk-[A-Za-z0-9_-]{16,256}|gh[pousr]_[A-Za-z0-9]{20,256})"),
}
_RELEVANT_HEADERS = frozenset({"content-type", "anthropic-version", "anthropic-beta"})
_REQUEST_FIELDS = {
    "openai-chat": frozenset({
        "model",
        "messages",
        "tools",
        "tool_choice",
        "parallel_tool_calls",
        "response_format",
        "stream",
        "stream_options",
        "max_tokens",
        "max_completion_tokens",
        "temperature",
        "top_p",
        "stop",
        "n",
        "presence_penalty",
        "frequency_penalty",
        "logit_bias",
        "logprobs",
        "top_logprobs",
        "seed",
        "user",
        "service_tier",
        "reasoning_effort",
        "verbosity",
        "store",
        "metadata",
        "prediction",
        "prompt_cache_key",
        "prompt_cache_retention",
        "prompt_cache_options",
        "safety_identifier",
        "extra_body",
    }),
    "openai-responses": frozenset({
        "model",
        "input",
        "instructions",
        "tools",
        "tool_choice",
        "parallel_tool_calls",
        "text",
        "stream",
        "max_output_tokens",
        "temperature",
        "top_p",
        "store",
        "metadata",
        "reasoning",
        "include",
        "service_tier",
        "prompt_cache_key",
        "prompt_cache_retention",
        "prompt_cache_options",
        "safety_identifier",
        "user",
        "truncation",
    }),
    "anthropic-messages": frozenset({
        "model",
        "messages",
        "system",
        "tools",
        "tool_choice",
        "stream",
        "max_tokens",
        "temperature",
        "top_p",
        "top_k",
        "stop_sequences",
        "metadata",
        "thinking",
        "cache_control",
        "service_tier",
        "output_config",
    }),
    "openai-embeddings": frozenset({"model", "input", "dimensions", "encoding_format", "user"}),
    "minimax-embeddings": frozenset({"model", "texts", "type"}),
    "jev-rerank": frozenset({"model", "state", "questions"}),
}


def _anthropic_cache_carriers(value: dict[str, object]) -> list[dict[str, object]]:
    """Only protocol cache positions, excluding properties of user tool schemas."""

    carriers: list[dict[str, object]] = [value]
    for key in ("system", "tools"):
        items = value.get(key)
        if isinstance(items, list):
            carriers.extend(cast(dict[str, object], item) for item in items if isinstance(item, dict))
    messages = value.get("messages")
    if isinstance(messages, list):
        for message in messages:
            content = message.get("content") if isinstance(message, dict) else None
            if isinstance(content, list):
                carriers.extend(cast(dict[str, object], item) for item in content if isinstance(item, dict))
    return carriers


def _cache_constraints(value: dict[str, object], profile: UpstreamProfile) -> bool:
    """Check native fields at protocol positions, never a user's JSON schema."""

    if "prompt_cache_retention" in value and value.get("prompt_cache_retention") not in profile.allowed_cache_retention:
        return False
    if "prompt_cache_options" in value:
        options = value.get("prompt_cache_options")
        if not isinstance(options, dict) or options.get("ttl", "30m") not in profile.allowed_cache_retention:
            return False
    explicit_retention = "prompt_cache_retention" in value or "prompt_cache_options" in value
    if (
        "prompt_cache_key" in value
        and not explicit_retention
        and "provider-default" not in profile.allowed_cache_retention
    ):
        return False
    if profile.protocol != "anthropic-messages":
        return True
    for carrier in _anthropic_cache_carriers(value):
        if "cache_control" in carrier:
            control = carrier.get("cache_control")
            if (
                not isinstance(control, dict)
                or control.get("type") != "ephemeral"
                or control.get("ttl", "5m") not in profile.allowed_cache_retention
            ):
                return False
    return True


class PrivacyPolicy:
    """Inspect the supported raw and decoded representation without side effects."""

    def __init__(self, config: InferenceProtectionConfig) -> None:
        self.config = config
        self._values = tuple(item.get_secret_value() for item in config.forbidden_values)

    @property
    def enforcing(self) -> bool:
        return self.config.mode == "enforcing"

    def decision(
        self,
        phase: PrivacyPhase,
        decision: Literal["allow", "block", "withhold"],
        *reasons: str,
        complete: bool = False,
    ) -> PrivacyDecision:
        return PrivacyDecision(
            decision=decision,
            phase=phase,
            reason_codes=tuple(reasons),
            coverage=PrivacyCoverage(complete=complete),
            policy_version=self.config.policy_version,
            mode=self.config.mode,
        )

    def forbidden(self, values: tuple[str, ...]) -> bool:
        return any(secret in value for value in values for secret in self._values) or any(
            _PATTERNS[name].search(value) is not None for value in values for name in self.config.forbidden_patterns
        )

    def select_target(self, body: bytes, endpoint: str) -> tuple[str, UpstreamProfile] | None:
        """Match one trusted profile to the actual model and destination."""

        try:
            value = json.loads(body)
        except (ValueError, UnicodeError, RecursionError):
            return None
        model = value.get("model") if isinstance(value, dict) else None
        matches = [
            (name, target)
            for name, target in self.config.targets.items()
            if target.endpoint == endpoint and target.model == model
        ]
        return matches[0] if len(matches) == 1 else None

    def inspect_structured_input(self, text: str) -> PrivacyDecision:
        """Check declared stage JSON before or after a representation change.

        Unlike arbitrary user prose, a declared JSON input must parse within
        this policy's bounds. This content check never grants final admission:
        target, protocol, headers and the complete request budget remain owned
        by ``inspect_json`` at the transport boundary.
        """

        if self.config.mode == "disabled":
            return self.decision("before_send", "allow", "protection_disabled")
        try:
            if len(text.encode("utf-8")) > self.config.max_payload_bytes:
                return self.decision("before_send", "withhold", "payload_too_large")
            if self.forbidden((text,)):
                return self.decision("before_send", "block", "forbidden_content")
            value, strings, duplicate = parse_json_evidence(text, self.config.max_json_depth)
            nested, repeated = nested_json_text(strings, self.config.max_json_depth)
        except (ValueError, RecursionError, UnicodeError):
            return self.decision("before_send", "withhold", "json_invalid_or_too_deep")
        if self.forbidden(strings + nested + joined_content_text(value)):
            return self.decision("before_send", "block", "forbidden_content")
        if duplicate or repeated:
            return self.decision("before_send", "withhold", "json_duplicate_key")
        return self.decision("before_send", "allow", "content_checked")

    def inspect_json(  # noqa: C901 - every branch returns a distinct admission failure
        self,
        body: bytes,
        *,
        phase: PrivacyPhase,
        target_profile: str,
        headers: Mapping[str, str] | None = None,
        endpoint: str | None = None,
        completion: Literal["complete", "incomplete", "unknown"] = "complete",
    ) -> PrivacyDecision:
        if self.config.mode == "disabled":
            return self.decision(phase, "allow", "protection_disabled")
        profile = self.config.targets.get(target_profile)
        if profile is None:
            return self.decision(phase, "withhold", "target_unsupported")
        if endpoint is not None and endpoint != profile.endpoint:
            return self.decision(phase, "withhold", "target_mismatch")
        limit = self.config.max_payload_bytes if phase == "before_send" else self.config.max_response_bytes
        if len(body) > limit:
            return self.decision(phase, "withhold", "payload_too_large")
        if headers:
            normalized_headers = {key.lower(): value for key, value in headers.items()}
            if len(normalized_headers) != len(headers):
                return self.decision(phase, "withhold", "header_duplicate")
            if set(normalized_headers) - _RELEVANT_HEADERS:
                return self.decision(phase, "withhold", "header_unsupported")
            content_type = normalized_headers.get("content-type", "application/json").split(";", 1)[0].strip()
            if content_type != "application/json":
                return self.decision(phase, "withhold", "carrier_unsupported")
            if self.forbidden(tuple(normalized_headers.values())):
                return self.decision(phase, "block", "forbidden_content", complete=True)
        try:
            text = body.decode("utf-8", errors="strict")
        except UnicodeError:
            return self.decision(phase, "withhold", "encoding_unsupported")
        if self.forbidden((text,)):
            return self.decision(phase, "block", "forbidden_content", complete=True)
        try:
            value, strings, duplicate = parse_json_evidence(text, self.config.max_json_depth)
            nested, repeated = nested_json_text(strings, self.config.max_json_depth)
            duplicate = duplicate or repeated
        except (ValueError, RecursionError, UnicodeError):
            return self.decision(phase, "withhold", "json_invalid_or_too_deep")
        if self.forbidden(strings + nested + joined_content_text(value)):
            return self.decision(phase, "block", "forbidden_content", complete=True)
        if duplicate:
            return self.decision(phase, "withhold", "json_duplicate_key")
        if contains_opaque_content(value, protocol=profile.protocol):
            return self.decision(phase, "withhold", "content_unsupported")
        if not all(
            complete_tool_arguments(item, self.config.max_json_depth)
            for item in tool_arguments(value, profile.protocol)
        ):
            return self.decision(phase, "withhold", "tool_arguments_incomplete")
        if phase == "before_send":
            if value.get("model") != profile.model:
                return self.decision(phase, "withhold", "model_mismatch")
            if set(value) - _REQUEST_FIELDS[profile.protocol]:
                return self.decision(phase, "withhold", "content_field_unsupported")
            if value.get("stream") is True and (not profile.allow_streaming or profile.protocol != "openai-chat"):
                return self.decision(phase, "withhold", "streaming_unsupported")
            if (
                "extra_body" in value
                or value.get("store") is True
                or (profile.protocol == "openai-responses" and value.get("store") is not False)
                or value.get("truncation") == "auto"
            ):
                return self.decision(phase, "withhold", "provider_state_unsupported")
            if "n" in value and json_unsigned_integer(value["n"]) != 1:
                return self.decision(phase, "withhold", "multiple_choices_unsupported")
            if profile.protocol in {"openai-embeddings", "minimax-embeddings"}:
                inputs = value.get("input", value.get("texts"))
                if not isinstance(inputs, str) and not (
                    isinstance(inputs, list) and all(isinstance(item, str) for item in inputs)
                ):
                    return self.decision(phase, "withhold", "embedding_content_unsupported")
            if not _cache_constraints(value, profile):
                return self.decision(phase, "withhold", "cache_retention_unapproved")
            budget = self._budget(value, body, strings, profile)
            if budget is not None:
                return self.decision(phase, "withhold", budget)
        else:
            if "model" in value and value["model"] not in (profile.model, *profile.response_models):
                return self.decision(phase, "withhold", "response_model_mismatch")
            if completion != "complete" or not complete_response(value, profile.protocol):
                return self.decision(phase, "withhold", "response_incomplete")
        return self.decision(phase, "allow", complete=True)

    def _budget(
        self, value: dict[str, object], body: bytes, strings: tuple[str, ...], profile: UpstreamProfile
    ) -> str | None:
        token_fields = ("max_tokens", "max_completion_tokens", "max_output_tokens")
        configured = [value[field] for field in token_fields if field in value]
        output_limit = max((json_unsigned_integer(item) or 0 for item in configured), default=0)
        if configured and any(
            json_unsigned_integer(item) is None or json_unsigned_integer(item) == 0 for item in configured
        ):
            return "output_limit_invalid"
        generating = profile.protocol in {"openai-chat", "openai-responses", "anthropic-messages"}
        if (
            self.enforcing
            and generating
            and (profile.context_window_tokens is None or profile.max_output_tokens is None)
        ):
            return "budget_unconfigured"
        if (
            profile.max_output_tokens is not None
            and generating
            and (not output_limit or output_limit > profile.max_output_tokens)
        ):
            return "output_limit_required"
        if profile.context_window_tokens is not None:
            if not profile.validated_utf8_token_bound:
                return "token_bound_unverified"
            if generating and not output_limit:
                return "output_limit_required"
            # A deployment must separately validate this upper bound for its
            # tokenizer/template. Char/4 is not a hard window guarantee.
            bound = max(len(body), sum(len(item.encode("utf-8")) for item in strings))
            if bound + profile.protocol_token_reserve + output_limit > profile.context_window_tokens:
                return "context_window_exceeded"
        return None


__all__ = [
    "InferenceProtectionConfig",
    "PrivacyCoverage",
    "PrivacyDecision",
    "PrivacyPolicy",
    "UpstreamProfile",
]
