"""Strict scenario and replay evidence models."""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_evals.otel import SpanNode


class EvidenceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Provenance(EvidenceModel):
    source: str
    revision: str
    selection: str
    case_ids: tuple[str, ...] = Field(min_length=1)


class SessionSpec(EvidenceModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    input: str = Field(min_length=1)
    expected_memory: tuple[str, ...] = ()
    expected_context: tuple[str, ...] = ()
    expected_answer: str | None = None


class ScenarioSpec(EvidenceModel):
    schema_: Literal["powercontext.session-replay/v1"] = Field(alias="schema")
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    provenance: Provenance | None = None
    sessions: tuple[SessionSpec, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def require_unique_session_ids(self) -> ScenarioSpec:
        session_ids = [session.id for session in self.sessions]
        if len(session_ids) != len(set(session_ids)):
            raise ValueError("Replay session IDs must be unique")  # noqa: TRY003
        return self


class RunEnvironment(EvidenceModel):
    mode: Literal["acceptance", "live", "offline-rescore"]
    commit: str
    database: str
    agent_model: str | None = None
    generation_model: str | None = None
    embedding_profile: str | None = None
    judge_model: str | None = None
    started_at: datetime


class MemoryEntrySnapshot(EvidenceModel):
    entry_id: str
    entry_version_id: str
    version: int
    kind: str
    text: str
    state: str


class MemorySnapshot(EvidenceModel):
    entries: tuple[MemoryEntrySnapshot, ...] = ()


class PreparedContextSnapshot(EvidenceModel):
    status: str
    content: str = ""


class SessionObservation(EvidenceModel):
    id: str
    agent_session_id: str
    status: Literal["completed", "failed"]
    error: str | None = None
    prepared_context: PreparedContextSnapshot
    output: str = ""
    memory_after: MemorySnapshot


class ReplayObservation(EvidenceModel):
    schema_: Literal["powercontext.session-replay-evidence/v1"] = Field(
        default="powercontext.session-replay-evidence/v1",
        alias="schema",
    )
    run_id: str
    environment: RunEnvironment
    scenario: ScenarioSpec
    status: Literal["completed", "failed"]
    errors: tuple[str, ...] = ()
    memory_before: MemorySnapshot
    memory_after: MemorySnapshot
    sessions: tuple[SessionObservation, ...]
    spans: tuple[SpanNode, ...] = ()


def load_scenario(path: Path) -> ScenarioSpec:
    scenario = ScenarioSpec.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    if scenario.provenance is not None:
        source = Path(scenario.provenance.source)
        fingerprint = hashlib.sha256(source.read_bytes()).hexdigest()
        if fingerprint != scenario.provenance.revision:
            raise ValueError(f"Scenario source fingerprint changed: {source}")  # noqa: TRY003
    return scenario
