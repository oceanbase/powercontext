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

"""Request-local execution evidence; no content or extra retrieval is stored."""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import asdict
from hashlib import sha256
from typing import Any
from uuid import uuid4

import rfc8785

from powercontext.artifacts import ArtifactAddress, ArtifactRef
from powercontext.builtin.artifacts.memory.canonical import normalize_query
from powercontext.builtin.artifacts.prompt.models import ResolvedPrompt
from powercontext.builtin.runtime.models import PrepareContextRequest, PreparedContext
from powercontext.builtin.runtime.prepared_code import CodeEvidenceRef
from powercontext.builtin.runtime.prepared_context import PreparedContextBuild
from powercontext.builtin.runtime.receipt import (
    CodeEvidence,
    OmittedGroup,
    PreparedContextReceipt,
    PromptIdentity,
    RecallRound,
    RerankConfig,
    RerankGroup,
    RetrievalEvidence,
    SearchGroup,
    SelectedItem,
    StageTiming,
)

# The pinned pydantic-ai ModelSettings version supports additional provider-specific keys.
# Reject those rather than publish an incomplete or credential-bearing configuration.
_SAFE_SETTINGS: dict[str, type] = {
    "max_tokens": int,
    "temperature": float,
    "top_p": float,
    "seed": int,
    "presence_penalty": float,
    "frequency_penalty": float,
    "parallel_tool_calls": bool,
    "allow_continuations": bool,
    "output_tokens_limit": int,
    "top_k": int,
    "timeout": float,
    "tool_choice": str,
    "algorithm": str,
    "algorithm_version": str,
}


def _digest(data: bytes) -> str:
    return f"sha256:{sha256(data).hexdigest()}"


def _safe_settings(settings: dict[str, Any] | None) -> dict[str, str | int | float | bool]:
    result: dict[str, str | int | float | bool] = {}
    for name, value in (settings or {}).items():
        expected = _SAFE_SETTINGS.get(name)
        if expected is None or isinstance(value, bool) != (expected is bool):
            raise ValueError("unsupported rerank setting")  # noqa: TRY003
        if expected is float:
            if not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError("invalid numeric rerank setting")  # noqa: TRY003
        elif not isinstance(value, expected):
            raise ValueError("invalid rerank setting type")  # noqa: TRY003
        if name == "tool_choice" and value not in {"none", "required", "auto"}:
            raise ValueError("unsupported rerank tool choice")  # noqa: TRY003
        if name in {"algorithm", "algorithm_version"} and not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", value):
            raise ValueError("unsafe rerank algorithm identity")  # noqa: TRY003
        result[name] = value
    return result


def _prompt_identity(prompt: ResolvedPrompt | None) -> PromptIdentity | None:
    if prompt is None:
        return None
    return PromptIdentity(
        scope_id=prompt.scope_id,
        key=prompt.key,
        definition_version=prompt.definition_version,
        builtin_version=prompt.builtin_version,
        selection=prompt.selection,
        selected_version=prompt.selected_version,
        compiled_digest=prompt.compiled_digest,
        artifact_address=(
            None if prompt.artifact is None else ArtifactAddress(scope_id=prompt.scope_id, artifact=prompt.artifact)
        ),
    )


def _rerank_record(
    configuration: dict[str, Any], prompt: ResolvedPrompt | None
) -> tuple[dict[str, Any], PromptIdentity | None]:
    if set(configuration) != {"policy_id", "model", "effective_settings", "timeout_seconds", "max_requests"}:
        raise ValueError("incomplete rerank configuration")  # noqa: TRY003
    model = configuration["model"]
    if model is not None and (
        not isinstance(model, str) or not re.fullmatch(r"[A-Za-z0-9_.:/-]{1,128}", model) or "://" in model
    ):
        raise ValueError("unsafe model identity")  # noqa: TRY003
    if not isinstance(configuration["policy_id"], str) or not re.fullmatch(
        r"[a-zA-Z0-9._/-]{1,128}", configuration["policy_id"]
    ):
        raise ValueError("unsafe policy identity")  # noqa: TRY003
    if model is not None and prompt is None:
        raise ValueError("model rerank prompt missing")  # noqa: TRY003
    settings = _safe_settings(configuration["effective_settings"])
    if model is None and not {"algorithm", "algorithm_version"} <= settings.keys():
        raise ValueError("deterministic reranker identity missing")  # noqa: TRY003
    selected_prompt = _prompt_identity(prompt) if model is not None else None
    return {
        "version": 1,
        "policy_id": configuration["policy_id"],
        "model": model,
        "effective_settings": settings,
        "timeout_seconds": configuration["timeout_seconds"],
        "max_requests": configuration["max_requests"],
        "prompt": None if selected_prompt is None else selected_prompt.model_dump(mode="json"),
    }, selected_prompt


class ReceiptEvidence:
    """Collect bounded summaries during the same prepare that produces the injection."""

    def __init__(self) -> None:
        self.round = 0
        self.rounds: list[RecallRound] = []
        self.searches: Counter[tuple[int, str, str, str, str]] = Counter()
        self.reranks: Counter[tuple[int, str, str, str]] = Counter()
        self.configs: dict[str, RerankConfig] = {}
        self.omitted: Counter[tuple[str, str]] = Counter()
        self.stages: dict[str, tuple[float, int]] = {}
        self.incomplete: str | None = None
        self.fallback_scopes: set[tuple[str, str]] = set()

    def search(
        self, family: str, mode: str | None, outcome: str, fallback: str = "none", *, scope_id: str | None = None
    ) -> None:
        try:
            if outcome == "completed" and mode is None:
                self.incomplete = "search-mode-missing"
                return
            if scope_id is not None and family in {"atomic-memory", "topic-memory"}:
                key = (family, scope_id)
                if fallback in {"inference_timeout", "inference_unavailable"}:
                    self.fallback_scopes.add(key)
                elif fallback == "none" and mode in {"fts", "text"} and self.round > 0 and key in self.fallback_scopes:
                    fallback = "reused_fts_fallback"
            self.searches[(self.round, family, "fts" if mode == "text" else mode or "none", outcome, fallback)] += 1
        except Exception as error:
            self.incomplete = type(error).__name__

    def stage(self, stage: str, elapsed_ms: float) -> None:
        duration, count = self.stages.get(stage, (0.0, 0))
        self.stages[stage] = (duration + max(0.0, elapsed_ms), count + 1)

    def rerank(
        self,
        *,
        configuration: dict[str, Any] | None,
        prompt: ResolvedPrompt | None,
        outcome: str,
        fallback: str,
        omitted_count: int = 0,
    ) -> None:
        if configuration is None:
            self.incomplete = "rerank-config-missing"
            return
        try:
            record, selected_prompt = _rerank_record(configuration, prompt)
            digest = _digest(rfc8785.dumps(record))
            config = RerankConfig.model_validate({
                "config_id": digest,
                "config_digest": digest,
                "non_deterministic": record["model"] is not None,
                "prompt": selected_prompt,
                **{key: value for key, value in record.items() if key not in {"version", "prompt"}},
            })
            self.configs[digest] = config
            self.reranks[(self.round, digest, outcome, fallback)] += 1
            self.omitted[("atomic-memory", "rerank_not_selected")] += omitted_count
        except Exception as error:
            self.incomplete = type(error).__name__

    def build(
        self, request: PrepareContextRequest, context: PreparedContext, build: PreparedContextBuild, scope_id: str
    ) -> PreparedContextReceipt:
        if self.incomplete is not None:
            raise ValueError(self.incomplete)
        if not self.rounds:
            self.rounds.append(RecallRound(round=0, outcome="completed", retained=True))
        if len(build.fragments) != len(build.origins) + len(build.code_origins):
            raise ValueError("selected-fragments-incomplete")
        expected = (
            tuple(
                ArtifactAddress(scope_id=scope_id, artifact=origin) if isinstance(origin, ArtifactRef) else origin
                for origin in build.origins
            )
            + build.code_origins
        )
        selected: list[SelectedItem] = []
        for fragment, origin in zip(build.fragments, expected, strict=True):
            actual = (
                ArtifactAddress(scope_id=scope_id, artifact=fragment.origin)
                if isinstance(fragment.origin, ArtifactRef)
                else fragment.origin
            )
            if actual != origin:
                raise ValueError("selected-identities-differ")
            if isinstance(origin, CodeEvidenceRef):
                selected.append(
                    SelectedItem.model_validate({
                        "kind": "code",
                        "code_evidence": CodeEvidence.model_validate(asdict(origin)),
                        "rendered_bytes": fragment.rendered_bytes,
                        "truncated": fragment.truncated,
                    })
                )
            else:
                selected.append(
                    SelectedItem.model_validate({
                        "kind": fragment.kind,
                        "artifact_address": origin,
                        "rendered_bytes": fragment.rendered_bytes,
                        "truncated": fragment.truncated,
                    })
                )
        omissions = self.omitted.copy()
        omissions.update({(family, reason): count for family, reason, count in build.omitted})
        receipt = PreparedContextReceipt(
            receipt_id=str(uuid4()),
            query_digest=_digest(normalize_query(request.query).encode("utf-8")),
            content_digest=None if context.content is None else _digest(context.content.encode("utf-8")),
            requested_max_bytes=request.max_bytes,
            used_bytes=context.content_bytes,
            truncated=any(item.truncated for item in selected),
            non_deterministic=any(config.non_deterministic for config in self.configs.values()),
            retrieval=RetrievalEvidence(
                rounds=tuple(self.rounds),
                searches=tuple(
                    SearchGroup.model_validate({
                        "round": r,
                        "family": f,
                        "mode": m,
                        "outcome": o,
                        "fallback_reason": b,
                        "count": c,
                    })
                    for (r, f, m, o, b), c in sorted(self.searches.items())
                ),
                rerank_configs=tuple(self.configs[key] for key in sorted(self.configs)),
                reranks=tuple(
                    RerankGroup.model_validate({
                        "round": r,
                        "config_id": id_,
                        "outcome": o,
                        "fallback_reason": b,
                        "count": c,
                    })
                    for (r, id_, o, b), c in sorted(self.reranks.items())
                ),
            ),
            selected=tuple(selected),
            omitted=tuple(
                OmittedGroup.model_validate({"family": f, "reason": r, "count": c})
                for (f, r), c in sorted(omissions.items())
                if c > 0
            ),
            stages=tuple(
                StageTiming(stage=stage, duration_ms=round(duration, 3), count=count)
                for stage, (duration, count) in sorted(self.stages.items())
            ),
        )
        # FastAPI's JSONResponse uses compact UTF-8 JSON.
        wire = json.dumps(receipt.model_dump(mode="json", by_alias=True), separators=(",", ":"), ensure_ascii=False)
        if len(wire.encode("utf-8")) > 8192:
            raise ValueError("receipt-byte-limit")
        return receipt
