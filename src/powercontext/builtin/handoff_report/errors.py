"""Typed failures owned by the optional Handoff Report feature."""

from __future__ import annotations

from powercontext.errors import PowerContextError


class HandoffReportError(PowerContextError):
    """Base class for failures isolated to Handoff Report operations."""


class HandoffReportBusyError(HandoffReportError):
    """Raised when repeated head reads cannot form an optimistic-stable selection."""

    def __init__(self, attempts: int) -> None:
        self.attempts = attempts
        super().__init__(f"Handoff heads remained unstable after {attempts} attempts")


class HandoffReportInconsistentError(HandoffReportError):
    """Raised when an adapter cannot return the exact Handoff frozen in selection."""

    def __init__(self, scope_id: str) -> None:
        self.scope_id = scope_id
        super().__init__(f"the frozen Handoff selection became inconsistent for scope {scope_id!r}")


class HandoffReportEvidenceCheckUnavailableError(HandoffReportError):
    """Raised when a read adapter has no independent evidence-check capability."""


class HandoffReportTooLargeError(HandoffReportError):
    """Raised when an untruncated report exceeds a deterministic resource limit."""

    def __init__(
        self,
        *,
        selected_workstreams: int,
        selected_activities: int,
        estimated_bytes: int | None = None,
    ) -> None:
        self.selected_workstreams = selected_workstreams
        self.selected_activities = selected_activities
        self.estimated_bytes = estimated_bytes
        super().__init__("the Handoff Report exceeds the configured projection limit")


class HandoffReportCatalogArgumentError(HandoffReportError, ValueError):
    """Raised when a catalog operation receives an invalid control value."""

    def __init__(self, field: str, detail: str) -> None:
        self.field = field
        self.detail = detail
        super().__init__(f"invalid Handoff Report catalog argument {field}: {detail}")


class InvalidStoredCatalogError(HandoffReportError):
    """Raised when a persisted catalog descriptor is malformed or inconsistent."""

    def __init__(self, kind: str, detail: str) -> None:
        self.kind = kind
        self.detail = detail
        super().__init__(f"invalid stored Handoff Report {kind}: {detail}")


class ProjectNotFoundError(HandoffReportError, LookupError):
    """Raised when a Report Project is absent."""

    code = "project_not_found"

    def __init__(self, project_id: str) -> None:
        self.project_id = project_id
        super().__init__(f"Report Project {project_id!r} was not found")


class WorkstreamNotFoundError(HandoffReportError, LookupError):
    """Raised when a Report Workstream is absent."""

    code = "scope_not_grouped"

    def __init__(self, scope_id: str) -> None:
        self.scope_id = scope_id
        super().__init__(f"Report Workstream {scope_id!r} was not found")


class ProjectConflictError(HandoffReportError, ValueError):
    """Raised when Project CAS or uniqueness validation fails."""

    code = "project_conflict"

    def __init__(
        self,
        project_id: str,
        expected_version: int | None,
        current_version: int | None,
        *,
        detail: str = "Project version or key conflicts with the current catalog",
    ) -> None:
        self.project_id = project_id
        self.expected_version = expected_version
        self.current_version = current_version
        self.detail = detail
        super().__init__(detail)


class WorkstreamConflictError(HandoffReportError, ValueError):
    """Raised when Workstream CAS or uniqueness validation fails."""

    code = "workstream_conflict"

    def __init__(
        self,
        scope_id: str,
        expected_version: int | None,
        current_version: int | None,
        *,
        detail: str = "Workstream version or key conflicts with the current catalog",
    ) -> None:
        self.scope_id = scope_id
        self.expected_version = expected_version
        self.current_version = current_version
        self.detail = detail
        super().__init__(detail)


class ScopeAlreadyGroupedError(HandoffReportError, ValueError):
    """Raised when a scope is already a member of another Project."""

    code = "scope_already_grouped"

    def __init__(self, scope_id: str, project_id: str) -> None:
        self.scope_id = scope_id
        self.project_id = project_id
        super().__init__(f"scope {scope_id!r} already belongs to Project {project_id!r}")


class WorkspaceBindingNotFoundError(HandoffReportError, LookupError):
    """Raised when a workspace has no confirmed Project binding."""

    code = "workspace_not_bound"

    def __init__(self, workspace_instance_id: str) -> None:
        self.workspace_instance_id = workspace_instance_id
        super().__init__(f"workspace {workspace_instance_id!r} has no confirmed Report binding")


class WorkspaceBindingConflictError(HandoffReportError, ValueError):
    """Raised when workspace binding CAS or single-binding rules fail."""

    code = "workspace_binding_conflict"

    def __init__(
        self,
        workspace_instance_id: str,
        expected_version: int | None,
        current_version: int | None,
        *,
        detail: str = "workspace binding version conflicts with the current catalog",
    ) -> None:
        self.workspace_instance_id = workspace_instance_id
        self.expected_version = expected_version
        self.current_version = current_version
        self.detail = detail
        super().__init__(detail)


__all__ = [
    "HandoffReportBusyError",
    "HandoffReportCatalogArgumentError",
    "HandoffReportError",
    "HandoffReportEvidenceCheckUnavailableError",
    "HandoffReportInconsistentError",
    "HandoffReportTooLargeError",
    "InvalidStoredCatalogError",
    "ProjectConflictError",
    "ProjectNotFoundError",
    "ScopeAlreadyGroupedError",
    "WorkspaceBindingConflictError",
    "WorkspaceBindingNotFoundError",
    "WorkstreamConflictError",
    "WorkstreamNotFoundError",
]
