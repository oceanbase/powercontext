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

"""Immutable domain values owned by the optional Handoff Report feature."""

from __future__ import annotations

import posixpath
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal, TypeAlias
from unicodedata import normalize
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

from powercontext.artifacts import ArtifactRef
from powercontext.limits import MAX_SCOPE_ID_LENGTH

MAX_REPORT_ID_LENGTH = 256
MAX_PROJECT_KEY_LENGTH = 64
MAX_WORKSTREAM_KEY_LENGTH = 64
MAX_REPORT_TITLE_LENGTH = 256
MAX_REPORT_DESCRIPTION_LENGTH = 2_000
MAX_REPORT_LABEL_LENGTH = 128
MAX_REPORT_PROVIDER_LENGTH = 64
MAX_REPORT_AGENT_LABEL_LENGTH = 128
MAX_REPORT_SOURCE_SUMMARY_LENGTH = 2_000
MAX_REPORT_EXTERNAL_ID_LENGTH = 256
MAX_REPORT_URL_LENGTH = 2_048
MAX_REPORT_REPOSITORY_ID_LENGTH = 256
MAX_REPORT_NORMALIZED_REMOTE_LENGTH = 2_048
MAX_REPORT_SUBPATH_LENGTH = 1_024
MAX_WORKSPACE_INSTANCE_ID_LENGTH = 256
MAX_REPORT_EXTERNAL_REFS = 32
MAX_REPORT_LABELS = 32
MAX_REPORT_EVIDENCE_REFS = 32

ReportLocale: TypeAlias = Literal["zh-CN", "en"]
CatalogState: TypeAlias = Literal["included", "archived"]
WorkstreamKind: TypeAlias = Literal["feature", "bug", "refactor", "operations", "research", "other"]
ExternalReferenceKind: TypeAlias = Literal[
    "issue",
    "task",
    "pull_request",
    "branch",
    "feature",
    "release",
    "program",
    "other",
]
ReportActivitySource: TypeAlias = Literal[
    "handoff_observation",
    "git_commit",
    "git_worktree",
    "coding_session",
    "other",
]
ReportTimeBasis: TypeAlias = Literal[
    "source_reported",
    "host_observed",
    "first_seen",
    "current_only",
    "unknown",
]
ReportSelectionConsistency: TypeAlias = Literal["exact_input", "optimistic_stable"]
ReportSelectionStatus: TypeAlias = Literal["selected", "no_handoff"]
ReportActivityTrust: TypeAlias = Literal["untrusted_observation"]
HandoffReportTrust: TypeAlias = Literal["untrusted_history"]
GeneratedSummaryTrust: TypeAlias = Literal["generated_untrusted"]
RepositoryProvider: TypeAlias = Literal["github", "gitlab", "local", "other"]
WorkspaceBindingState: TypeAlias = Literal["confirmed", "detached"]


class _ReportValue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ExternalReference(_ReportValue):
    """A navigation or filtering reference that is not identity or evidence by itself."""

    kind: ExternalReferenceKind
    provider: Annotated[str, Field(max_length=MAX_REPORT_PROVIDER_LENGTH)]
    external_id: Annotated[str, Field(max_length=MAX_REPORT_EXTERNAL_ID_LENGTH)]
    url: Annotated[str, Field(max_length=MAX_REPORT_URL_LENGTH)] | None = None

    @field_validator("provider", "external_id", "url")
    @classmethod
    def require_trimmed_text(cls, value: str | None, info) -> str | None:
        return _require_optional_text(info.field_name, value)


class RepositoryRef(_ReportValue):
    """Credential-free repository identity hints attached to a workspace."""

    provider: RepositoryProvider
    repository_id: Annotated[str, Field(max_length=MAX_REPORT_REPOSITORY_ID_LENGTH)] | None = None
    normalized_remote: Annotated[str, Field(max_length=MAX_REPORT_NORMALIZED_REMOTE_LENGTH)] | None = None
    subpath: Annotated[str, Field(max_length=MAX_REPORT_SUBPATH_LENGTH)] | None = None

    @field_validator("repository_id", "normalized_remote", "subpath")
    @classmethod
    def require_trimmed_text(cls, value: str | None, info) -> str | None:
        return _require_optional_text(info.field_name, value)

    @model_validator(mode="after")
    def require_repository_signal(self) -> RepositoryRef:
        if self.repository_id is None and self.normalized_remote is None and self.subpath is None:
            raise ValueError("repository_ref must contain a repository id, remote, or subpath")  # noqa: TRY003
        if self.normalized_remote is not None and any(marker in self.normalized_remote for marker in ("@", "?", "#")):
            raise ValueError("normalized_remote must not contain credentials or query fragments")  # noqa: TRY003
        return self


class WorkspaceBinding(_ReportValue):
    """One CAS-versioned binding between a local checkout and a Report Project."""

    schema_version: Literal["powercontext.workspace-binding.v1"] = Field(
        default="powercontext.workspace-binding.v1",
        alias="schema",
    )
    workspace_instance_id: Annotated[str, Field(max_length=MAX_WORKSPACE_INSTANCE_ID_LENGTH)]
    project_id: Annotated[str, Field(max_length=MAX_REPORT_ID_LENGTH)]
    repository_ref: RepositoryRef
    state: WorkspaceBindingState = "confirmed"
    confirmed_at: datetime
    version: StrictInt = Field(ge=1)

    @field_validator("workspace_instance_id", "project_id")
    @classmethod
    def require_trimmed_text(cls, value: str, info) -> str:
        return _require_text(info.field_name, value)

    @field_validator("confirmed_at")
    @classmethod
    def normalize_confirmed_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("confirmed_at must include a UTC offset")  # noqa: TRY003
        return value.astimezone(UTC)


class ProjectDescriptor(_ReportValue):
    """One versioned Report-owned Project catalog snapshot."""

    schema_version: Literal["powercontext.project.v1"] = Field(
        default="powercontext.project.v1",
        alias="schema",
    )
    project_id: Annotated[str, Field(max_length=MAX_REPORT_ID_LENGTH)]
    project_key: Annotated[str, Field(max_length=MAX_PROJECT_KEY_LENGTH)]
    title: Annotated[str, Field(max_length=MAX_REPORT_TITLE_LENGTH)]
    description: Annotated[str, Field(max_length=MAX_REPORT_DESCRIPTION_LENGTH)] | None = None
    default_locale: ReportLocale = "zh-CN"
    timezone: Annotated[str, Field(max_length=MAX_REPORT_ID_LENGTH)]
    catalog_state: CatalogState = "included"
    version: StrictInt = Field(ge=1)

    @field_validator("project_id", "project_key", "title", "description")
    @classmethod
    def require_trimmed_text(cls, value: str | None, info) -> str | None:
        return _require_optional_text(info.field_name, value)

    @field_validator("timezone")
    @classmethod
    def require_iana_timezone(cls, value: str) -> str:
        _require_text("timezone", value)
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as error:
            raise ValueError("timezone must be a recognized IANA timezone") from error  # noqa: TRY003
        return value


class WorkstreamDescriptor(_ReportValue):
    """One versioned Report-owned descriptor for an existing Handoff scope."""

    schema_version: Literal["powercontext.workstream.v1"] = Field(
        default="powercontext.workstream.v1",
        alias="schema",
    )
    scope_id: Annotated[str, Field(max_length=MAX_SCOPE_ID_LENGTH)]
    project_id: Annotated[str, Field(max_length=MAX_REPORT_ID_LENGTH)]
    key: Annotated[str, Field(max_length=MAX_WORKSTREAM_KEY_LENGTH)] | None = None
    title: Annotated[str, Field(max_length=MAX_REPORT_TITLE_LENGTH)]
    kind: WorkstreamKind
    catalog_state: CatalogState = "included"
    external_refs: Annotated[tuple[ExternalReference, ...], Field(max_length=MAX_REPORT_EXTERNAL_REFS)] = ()
    labels: Annotated[tuple[str, ...], Field(max_length=MAX_REPORT_LABELS)] = ()
    version: StrictInt = Field(ge=1)

    @field_validator("scope_id", "project_id", "key", "title")
    @classmethod
    def require_trimmed_text(cls, value: str | None, info) -> str | None:
        return _require_optional_text(info.field_name, value)

    @field_validator("labels")
    @classmethod
    def require_valid_labels(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        for value in values:
            _require_text("label", value)
            if len(value) > MAX_REPORT_LABEL_LENGTH:
                raise ValueError(f"label must not exceed {MAX_REPORT_LABEL_LENGTH} characters")  # noqa: TRY003
        if len(set(values)) != len(values):
            raise ValueError("Workstream labels must be unique")  # noqa: TRY003
        return values

    @model_validator(mode="after")
    def require_unique_external_refs(self) -> WorkstreamDescriptor:
        if len(set(self.external_refs)) != len(self.external_refs):
            raise ValueError("Workstream external references must be unique")  # noqa: TRY003
        return self


class ActivityAgent(_ReportValue):
    """Untrusted Agent attribution reported by an activity source."""

    provider: Annotated[str, Field(max_length=MAX_REPORT_PROVIDER_LENGTH)] | None = None
    label: Annotated[str, Field(max_length=MAX_REPORT_AGENT_LABEL_LENGTH)] | None = None

    @field_validator("provider", "label")
    @classmethod
    def require_trimmed_text(cls, value: str | None, info) -> str | None:
        return _require_optional_text(info.field_name, value)

    @model_validator(mode="after")
    def require_attribution(self) -> ActivityAgent:
        if self.provider is None and self.label is None:
            raise ValueError("Activity Agent must contain a provider or label")  # noqa: TRY003
        return self


class ActivityVcsContext(_ReportValue):
    """Untrusted VCS display context observed for an activity."""

    branch: Annotated[str, Field(max_length=MAX_REPORT_TITLE_LENGTH)] | None = None
    head_revision: Annotated[str, Field(max_length=MAX_REPORT_ID_LENGTH)] | None = None

    @field_validator("branch", "head_revision")
    @classmethod
    def require_trimmed_text(cls, value: str | None, info) -> str | None:
        return _require_optional_text(info.field_name, value)

    @model_validator(mode="after")
    def require_context(self) -> ActivityVcsContext:
        if self.branch is None and self.head_revision is None:
            raise ValueError("Activity VCS context must contain a branch or head revision")  # noqa: TRY003
        return self


class ReportActivityEvent(_ReportValue):
    """One idempotent, untrusted observation in the independent Report activity store."""

    schema_version: Literal["powercontext.handoff-report-activity.v1"] = Field(
        default="powercontext.handoff-report-activity.v1",
        alias="schema",
    )
    event_id: Annotated[str, Field(max_length=MAX_REPORT_ID_LENGTH)]
    project_id: Annotated[str, Field(max_length=MAX_REPORT_ID_LENGTH)]
    scope_id: Annotated[str, Field(max_length=MAX_SCOPE_ID_LENGTH)] | None = None
    source: ReportActivitySource
    source_event_id: Annotated[str, Field(max_length=MAX_REPORT_ID_LENGTH)]
    source_ref: ExternalReference | None = None
    occurred_at: datetime | None = None
    observed_at: datetime
    time_basis: ReportTimeBasis
    title: Annotated[str, Field(max_length=MAX_REPORT_TITLE_LENGTH)] | None = None
    summary: Annotated[str, Field(max_length=MAX_REPORT_SOURCE_SUMMARY_LENGTH)] | None = None
    agent: ActivityAgent | None = None
    session_id: Annotated[str, Field(max_length=MAX_REPORT_ID_LENGTH)] | None = None
    vcs_context: ActivityVcsContext | None = None
    evidence_refs: Annotated[tuple[ExternalReference, ...], Field(max_length=MAX_REPORT_EVIDENCE_REFS)] = ()
    trust: ReportActivityTrust = "untrusted_observation"

    @field_validator("event_id", "project_id", "scope_id", "source_event_id", "title", "summary", "session_id")
    @classmethod
    def require_trimmed_text(cls, value: str | None, info) -> str | None:
        return _require_optional_text(info.field_name, value)

    @field_validator("occurred_at", "observed_at")
    @classmethod
    def require_aware_timestamp(cls, value: datetime | None, info) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError(f"{info.field_name} must include a UTC offset")  # noqa: TRY003
        return value

    @field_validator("observed_at")
    @classmethod
    def require_utc_observation(cls, value: datetime) -> datetime:
        if value.utcoffset() != timedelta(0):
            raise ValueError("observed_at must be UTC")  # noqa: TRY003
        return value

    @model_validator(mode="after")
    def validate_time_semantics_and_evidence(self) -> ReportActivityEvent:
        if self.time_basis == "source_reported":
            if self.occurred_at is None:
                raise ValueError("source-reported activity must contain occurred_at")  # noqa: TRY003
        elif self.occurred_at is not None:
            raise ValueError("occurred_at is only valid for source-reported activity")  # noqa: TRY003
        if len(set(self.evidence_refs)) != len(self.evidence_refs):
            raise ValueError("Activity evidence references must be unique")  # noqa: TRY003
        return self

    def effective_period_time(self) -> datetime | None:
        """Return the reportable event time, without inventing time for current or unknown activity."""

        if self.time_basis == "source_reported":
            return self.occurred_at
        if self.time_basis in {"host_observed", "first_seen"}:
            return self.observed_at
        return None


class ReportSelectionEntry(_ReportValue):
    """Freeze one Workstream descriptor revision and exact Handoff selection."""

    scope_id: Annotated[str, Field(max_length=MAX_SCOPE_ID_LENGTH)]
    workstream_revision: StrictInt = Field(ge=1)
    status: ReportSelectionStatus
    handoff_ref: ArtifactRef | None = None

    @field_validator("scope_id")
    @classmethod
    def require_scope_id(cls, value: str) -> str:
        return _require_text("scope_id", value)

    @model_validator(mode="after")
    def validate_selection(self) -> ReportSelectionEntry:
        if self.status == "selected":
            if self.handoff_ref is None:
                raise ValueError("selected Report entry must contain an exact Handoff reference")  # noqa: TRY003
            if self.handoff_ref.family != "handoff":
                raise ValueError("selected Report entry must reference the Handoff family")  # noqa: TRY003
        elif self.handoff_ref is not None:
            raise ValueError("no-handoff Report entry cannot contain a Handoff reference")  # noqa: TRY003
        return self


def normalized_sort_text(value: str) -> str:
    """Normalize user-visible text for deterministic, locale-independent ordering."""

    return normalize("NFC", value).casefold()


def workstream_sort_key(workstream: WorkstreamDescriptor) -> tuple[str, str]:
    """Sort descriptors by normalized title and stable scope identity."""

    return normalized_sort_text(workstream.title), workstream.scope_id


def activity_sort_key(event: ReportActivityEvent) -> tuple[bool, datetime, datetime, str]:
    """Sort reportable activity first and unknown/current activity deterministically last."""

    effective_time = event.effective_period_time()
    return effective_time is None, effective_time or event.observed_at, event.observed_at, event.event_id


def selection_sort_key(entry: ReportSelectionEntry) -> str:
    """Sort exact selections by canonical Workstream identity."""

    return entry.scope_id


def normalize_repository_ref(value: RepositoryRef) -> RepositoryRef:
    """Apply the versioned, credential-free normalization used for binding keys."""

    if not isinstance(value, RepositoryRef):
        raise TypeError("repository_ref must be a RepositoryRef")  # noqa: TRY003
    remote = _normalize_repository_remote(value.normalized_remote)
    subpath = _normalize_repository_subpath(value.subpath)

    return RepositoryRef(
        provider=value.provider,
        repository_id=value.repository_id,
        normalized_remote=remote,
        subpath=subpath,
    )


def _normalize_repository_remote(value: str | None) -> str | None:
    if value is None or "://" not in value:
        return None if value is None else value.rstrip("/")
    parsed = urlsplit(value)
    if parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment:
        raise ValueError("normalized_remote must not contain credentials or query fragments")  # noqa: TRY003
    if parsed.hostname is None:
        raise ValueError("normalized_remote must contain a host")  # noqa: TRY003
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError("normalized_remote must contain a valid port") from error  # noqa: TRY003
    host = parsed.hostname.lower()
    netloc = host if port is None else f"{host}:{port}"
    path = "/" + "/".join(part for part in parsed.path.split("/") if part not in {"", "."})
    return urlunsplit((parsed.scheme.lower(), netloc, path or "/", "", ""))


def _normalize_repository_subpath(value: str | None) -> str | None:
    if value is None:
        return None
    candidate = value.replace("\\", "/")
    if any(part == ".." for part in candidate.split("/")):
        raise ValueError("repository subpath must not contain parent traversal")  # noqa: TRY003
    normalized = posixpath.normpath(candidate)
    if normalized in {"", "."}:
        return "."
    if normalized.startswith("/"):
        raise ValueError("repository subpath must be relative")  # noqa: TRY003
    return normalized


def _require_text(field_name: str, value: str) -> str:
    if not value.strip():
        raise ValueError(f"{field_name} must contain non-whitespace text")  # noqa: TRY003
    if value != value.strip():
        raise ValueError(f"{field_name} must not contain leading or trailing whitespace")  # noqa: TRY003
    return value


def _require_optional_text(field_name: str, value: str | None) -> str | None:
    if value is not None:
        _require_text(field_name, value)
    return value


__all__ = [
    "MAX_PROJECT_KEY_LENGTH",
    "MAX_REPORT_AGENT_LABEL_LENGTH",
    "MAX_REPORT_DESCRIPTION_LENGTH",
    "MAX_REPORT_EVIDENCE_REFS",
    "MAX_REPORT_EXTERNAL_ID_LENGTH",
    "MAX_REPORT_EXTERNAL_REFS",
    "MAX_REPORT_ID_LENGTH",
    "MAX_REPORT_LABELS",
    "MAX_REPORT_LABEL_LENGTH",
    "MAX_REPORT_NORMALIZED_REMOTE_LENGTH",
    "MAX_REPORT_PROVIDER_LENGTH",
    "MAX_REPORT_REPOSITORY_ID_LENGTH",
    "MAX_REPORT_SOURCE_SUMMARY_LENGTH",
    "MAX_REPORT_SUBPATH_LENGTH",
    "MAX_REPORT_TITLE_LENGTH",
    "MAX_REPORT_URL_LENGTH",
    "MAX_WORKSPACE_INSTANCE_ID_LENGTH",
    "MAX_WORKSTREAM_KEY_LENGTH",
    "ActivityAgent",
    "ActivityVcsContext",
    "CatalogState",
    "ExternalReference",
    "ExternalReferenceKind",
    "GeneratedSummaryTrust",
    "HandoffReportTrust",
    "ProjectDescriptor",
    "ReportActivityEvent",
    "ReportActivitySource",
    "ReportActivityTrust",
    "ReportLocale",
    "ReportSelectionConsistency",
    "ReportSelectionEntry",
    "ReportSelectionStatus",
    "ReportTimeBasis",
    "RepositoryProvider",
    "RepositoryRef",
    "WorkspaceBinding",
    "WorkspaceBindingState",
    "WorkstreamDescriptor",
    "WorkstreamKind",
    "activity_sort_key",
    "normalize_repository_ref",
    "normalized_sort_text",
    "selection_sort_key",
    "workstream_sort_key",
]
