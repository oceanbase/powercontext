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

"""Validated catalog contracts for end-to-end workloads."""

from __future__ import annotations

import hashlib
import unicodedata
from pathlib import Path
from typing import ClassVar, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class CatalogModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Provenance(CatalogModel):
    source: str
    revision: str
    selection: str
    case_ids: tuple[str, ...] = Field(min_length=1)


class HarborDatasetSpec(CatalogModel):
    path: Path | None = None
    name: str | None = None
    version: str | None = None
    task_id: str = Field(min_length=1)
    checksum: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_one_source(self) -> HarborDatasetSpec:
        if (self.path is None) == (self.name is None):
            raise ValueError("A Harbor dataset requires exactly one of path or name")  # noqa: TRY003
        if self.path is not None and self.version is not None:
            raise ValueError("A local Harbor dataset cannot declare a version")  # noqa: TRY003
        return self


class BubExecutionSpec(CatalogModel):
    native_artifact_names: ClassVar[frozenset[str]] = frozenset({
        "acp-summary.json",
        "acp-events.jsonl",
        "trajectory.json",
    })

    type: Literal["bub"] = "bub"
    model: bool = False
    max_steps: int = Field(default=50, ge=1, le=200)
    max_tokens: int = Field(default=16384, ge=256)


class CaptureThresholds(CatalogModel):
    capture_coverage: float = Field(default=0, ge=0, le=1)
    groundedness: float = Field(default=0, ge=0, le=1)
    probe_coverage: float = Field(default=1, ge=0, le=1)
    minimum_in_run_contexts: int = Field(default=0, ge=0)


def normalize_context_fragment(value: str) -> str:
    """Normalize context fragments for stable case-insensitive matching."""

    return unicodedata.normalize("NFC", value.casefold())


class RecallProbeSpec(CatalogModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    query: str = Field(min_length=1, max_length=8192)
    expected_context: tuple[str, ...] = ()
    forbidden_context: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_context_fragments(self) -> RecallProbeSpec:
        for field_name in ("expected_context", "forbidden_context"):
            if any(not fragment.strip() for fragment in getattr(self, field_name)):
                raise ValueError(f"{field_name} fragments must be nonblank")  # noqa: TRY003

        expected = tuple(normalize_context_fragment(fragment) for fragment in self.expected_context)
        forbidden = tuple(normalize_context_fragment(fragment) for fragment in self.forbidden_context)
        if any(blocked in required for required in expected for blocked in forbidden):
            raise ValueError("forbidden_context cannot be contained in expected_context")  # noqa: TRY003
        return self


class MemoryEvaluationSpec(CatalogModel):
    capture_events: bool = False
    checkpoint_every_events: int = Field(default=5, ge=1, le=100)
    max_event_bytes: int = Field(default=8192, ge=512, le=32768)
    require_checkpoint: bool = False
    expected_memory: tuple[str, ...] = ()
    probes: tuple[RecallProbeSpec, ...] = Field(min_length=1)
    thresholds: CaptureThresholds = Field(default_factory=CaptureThresholds)

    @model_validator(mode="after")
    def require_unique_probe_ids(self) -> MemoryEvaluationSpec:
        probe_ids = [probe.id for probe in self.probes]
        if len(probe_ids) != len(set(probe_ids)):
            raise ValueError("Recall probe IDs must be unique")  # noqa: TRY003
        return self


class ExpectedExecutionSpec(CatalogModel):
    collect_all: Literal["completed", "failed", "skipped"] = Field(alias="collect-all")
    fail_fast: Literal["completed", "failed", "skipped"] = Field(alias="fail-fast")


class OutcomeEvaluationSpec(CatalogModel):
    expected_execution: ExpectedExecutionSpec


class E2ETask(CatalogModel):
    schema_: Literal["powercontext.e2e-task/v1"] = Field(alias="schema")
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    categories: tuple[str, ...] = Field(min_length=1)
    provenance: Provenance | None = None
    dataset: HarborDatasetSpec
    execution: BubExecutionSpec
    evaluation: MemoryEvaluationSpec | OutcomeEvaluationSpec


class TaskSelectionError(ValueError):
    """Report unknown IDs or categories at the catalog boundary."""

    def __init__(self, selector: str, values: set[str]) -> None:
        super().__init__(f"Unknown e2e workload {selector}: {sorted(values)!r}")


def load_tasks(path: Path) -> tuple[E2ETask, ...]:
    task_paths = sorted(path.glob("*.yaml")) if path.is_dir() else [path]
    tasks = tuple(E2ETask.model_validate(yaml.safe_load(item.read_text(encoding="utf-8"))) for item in task_paths)
    ids = [task.id for task in tasks]
    if not tasks:
        raise ValueError(f"No e2e workload manifests found at {path}")  # noqa: TRY003
    if len(ids) != len(set(ids)):
        raise ValueError("E2E workload IDs must be unique")  # noqa: TRY003
    for task in tasks:
        _validate_provenance(task)
    return tasks


def select_tasks(
    tasks: tuple[E2ETask, ...],
    *,
    ids: tuple[str, ...] = (),
    categories: tuple[str, ...] = (),
) -> tuple[E2ETask, ...]:
    requested_ids = set(ids)
    requested_categories = set(categories)
    available_ids = {task.id for task in tasks}
    available_categories = {category for task in tasks for category in task.categories}
    if missing_ids := requested_ids - available_ids:
        raise TaskSelectionError("IDs", missing_ids)
    if missing_categories := requested_categories - available_categories:
        raise TaskSelectionError("categories", missing_categories)
    if not requested_ids and not requested_categories:
        requested_categories = {"acceptance"}
    return tuple(
        task for task in tasks if task.id in requested_ids or requested_categories.intersection(task.categories)
    )


def _validate_provenance(task: E2ETask) -> None:
    if task.provenance is None:
        return
    source = Path(task.provenance.source)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    if digest != task.provenance.revision:
        raise ValueError(f"Task source fingerprint changed: {source}")  # noqa: TRY003
