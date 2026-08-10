from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from powercontext.builtin.handoff_report import (
    HandoffReportCatalog,
    ProjectNotFoundError,
    RepositoryRef,
    WorkspaceBindingConflictError,
    WorkspaceBindingNotFoundError,
    WorkspaceBindingService,
    normalize_repository_ref,
)
from powercontext.builtin.handoff_report.sqlite import HANDOFF_REPORT_TABLES
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile


def test_repository_ref_normalizes_safe_remote_and_relative_subpath() -> None:
    reference = RepositoryRef(
        provider="github",
        normalized_remote="HTTPS://GitHub.com/oceanbase/powercontext.git/",
        subpath="./services/api",
    )

    normalized = normalize_repository_ref(reference)

    assert normalized.normalized_remote == "https://github.com/oceanbase/powercontext.git"
    assert normalized.subpath == "services/api"

    with pytest.raises(ValidationError, match="credentials"):
        RepositoryRef(provider="github", normalized_remote="https://user@example.com/repo.git")
    with pytest.raises(ValueError, match="parent traversal"):
        normalize_repository_ref(
            RepositoryRef(provider="github", normalized_remote="https://github.com/org/repo.git", subpath="../api")
        )
    with pytest.raises(ValidationError, match="must contain"):
        RepositoryRef(provider="local")


def test_workspace_binding_requires_explicit_project_and_uses_cas_for_rebind() -> None:
    async def scenario() -> None:
        ids = iter(("prj-1", "prj-2"))
        catalog = HandoffReportCatalog(project_id_factory=ids.__next__)
        bindings = WorkspaceBindingService(catalog=catalog)
        reference = RepositoryRef(
            provider="github",
            repository_id="repo-1",
            normalized_remote="https://github.com/org/repo.git",
            subpath=".",
        )
        observed = datetime(2026, 8, 6, 2, tzinfo=timezone(timedelta(hours=8)))

        async with (
            SQLiteProfile.open(SQLiteConfig(), tables=HANDOFF_REPORT_TABLES) as profile,
            profile.database.transaction() as connection,
        ):
            first_project = await catalog.create_project(
                connection,
                project_key="first",
                title="First",
                timezone="UTC",
            )
            second_project = await catalog.create_project(
                connection,
                project_key="second",
                title="Second",
                timezone="UTC",
            )

            binding = await bindings.attach(
                connection,
                workspace_instance_id="ws-1",
                project_id=first_project.project_id,
                repository_ref=reference,
                expected_version=None,
                confirmed_at=observed,
            )
            assert binding.version == 1
            assert binding.state == "confirmed"
            assert binding.confirmed_at == observed.astimezone(UTC)
            assert binding.repository_ref.normalized_remote == "https://github.com/org/repo.git"
            assert await bindings.get(connection, "ws-1") == binding

            with pytest.raises(WorkspaceBindingConflictError, match="already has"):
                await bindings.attach(
                    connection,
                    workspace_instance_id="ws-1",
                    project_id=first_project.project_id,
                    repository_ref=reference,
                    expected_version=None,
                )

            refreshed = await bindings.attach(
                connection,
                workspace_instance_id="ws-1",
                project_id=first_project.project_id,
                repository_ref=reference,
                expected_version=1,
            )
            assert refreshed.version == 2

            with pytest.raises(WorkspaceBindingConflictError) as stale:
                await bindings.attach(
                    connection,
                    workspace_instance_id="ws-1",
                    project_id=first_project.project_id,
                    repository_ref=reference,
                    expected_version=1,
                )
            assert stale.value.current_version == 2

            detached = await bindings.detach(connection, "ws-1", expected_version=2)
            assert detached.state == "detached"
            assert detached.version == 3
            with pytest.raises(WorkspaceBindingNotFoundError):
                await bindings.get(connection, "ws-1")
            assert await bindings.get_record(connection, "ws-1") == detached

            rebound = await bindings.attach(
                connection,
                workspace_instance_id="ws-1",
                project_id=second_project.project_id,
                repository_ref=reference,
                expected_version=3,
            )
            assert rebound.project_id == second_project.project_id
            assert rebound.version == 4

    asyncio.run(scenario())


def test_workspace_attach_rejects_unknown_project_and_missing_exact_version() -> None:
    async def scenario() -> None:
        catalog = HandoffReportCatalog(project_id_factory=lambda: "prj-1")
        bindings = WorkspaceBindingService(catalog=catalog)
        reference = RepositoryRef(provider="local", subpath=".")

        async with (
            SQLiteProfile.open(SQLiteConfig(), tables=HANDOFF_REPORT_TABLES) as profile,
            profile.database.transaction() as connection,
        ):
            with pytest.raises(ProjectNotFoundError):
                await bindings.attach(
                    connection,
                    workspace_instance_id="ws-unknown",
                    project_id="missing",
                    repository_ref=reference,
                    expected_version=None,
                )
            with pytest.raises(WorkspaceBindingConflictError, match="missing"):
                await bindings.detach(connection, "ws-unknown", expected_version=1)
            with pytest.raises(WorkspaceBindingNotFoundError):
                await bindings.get(connection, "ws-unknown")

    asyncio.run(scenario())
