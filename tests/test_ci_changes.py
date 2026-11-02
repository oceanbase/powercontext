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

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import ci_changes
import pytest
from ci_changes import select_suites

ROOT = Path(__file__).parents[1]
RULES = tomllib.loads((ROOT / ".github/ci-paths.toml").read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("paths", "expected"),
    [
        (["docs/zh/docs/quickstart.md"], {"website", "docs_tests"}),
        (["docs/en/rfcs/0001_design.md", "README_CN.md"], {"website", "docs_tests"}),
        (["website/src/app/page.tsx"], {"website"}),
        (["tests/e2e/test_blog_site.py"], {"website", "tests"}),
        (["AGENTS.md", "CONTRIBUTING.md"], set()),
        (["deploy/helm/powercontext/templates/deployments.yaml"], {"helm"}),
        (["deploy/helm/powercontext/README.md"], {"helm"}),
        (["tests/test_helm_chart.py"], {"tests", "helm"}),
        (["tests/e2e/helm_probe.py"], {"tests", "helm"}),
        (["integrations/pi/plugins/powercontext/index.ts"], {"tests", "pi", "zcode"}),
        (["integrations/kimi-code/plugins/powercontext/skills/context/SKILL.md"], {"tests", "kimi"}),
        (["evaluation/coding/work_continuity/run.py"], {"evaluation"}),
        (["evaluation/agent_tool_recovery/run.py"], {"evaluation"}),
        (["evaluation/zcode_guidance/pin.py"], {"evaluation", "zcode"}),
        (["tests/e2e/test_kimi_code_host.py"], {"tests", "kimi"}),
        (["integrations/cindy/src/worker.ts"], {"tests", "cindy"}),
        (["tests/e2e/test_cindy_hooks_chain.py"], {"tests", "cindy"}),
        (["tests/e2e/cindy_hook_host.mjs"], {"tests", "cindy"}),
        (["integrations/dsh/plugins/powercontext/src/client.ts"], {"tests", "dsh", "cindy"}),
        (["integrations/dsh/plugins/powercontext/pnpm-lock.yaml"], {"tests", "dsh", "cindy"}),
        (["scripts/generate_js_operations.py"], {"tests", "dsh", "cindy"}),
        (["tests/builtin/persistence/test_mysql_schema.py"], {"tests", "database", "upgrade", "candidate_replay"}),
        (["tests/native/test_personal_service_lifecycle.py"], {"tests", "native"}),
        (["e2e/bub/compose.yaml"], {"harness"}),
        (["scripts/scan_e2e_evidence.sh"], {"tests", "harness"}),
        (["scripts/run_atomic_memory_clean_migration.py"], {"tests", "database", "upgrade"}),
        (["scripts/check_atomic_memory_migration_shards.py"], {"tests", "database"}),
        (["scripts/sync_dsh_plugin_tree.py"], {"tests", "dsh"}),
        (["desktop/ui/src/app/App.tsx"], {"desktop"}),
        (["release/upgrade-support.json"], {"upgrade", "packaging"}),
        ([".github/workflows/desktop.yml"], {"desktop"}),
        ([".github/workflows/managed-resource-candidates.yml"], {"managed_resources"}),
        (["scripts/produce_managed_resources.py"], {"managed_resources"}),
        (["scripts/build_intel_managed_wheel.py"], {"managed_resources"}),
        (["scripts/build_ops_bundle.py"], {"managed_resources", "upgrade", "packaging"}),
        (["scripts/ci_ops_bundle_smoke.py"], {"managed_resources", "upgrade"}),
        (["tests/test_managed_resource_producer.py"], {"tests", "managed_resources"}),
        (["tests/test_ops_resources.py"], {"tests", "managed_resources"}),
        (
            [".github/actions/cache-pnpm/action.yml"],
            {"tests", "desktop", "website", "dsh", "cindy", "pi", "opencode", "openclaw"},
        ),
        ([".github/workflows/native-service.yml"], {"native"}),
        ([".github/workflows/kimi-code-acceptance.yml"], {"kimi"}),
        ([".github/workflows/zcode-acceptance.yml"], {"zcode"}),
        ([".github/workflows/upgrade-release.yml"], {"upgrade"}),
        (
            [".github/actions/setup-upgrade-oceanbase/action.yml"],
            {"database", "upgrade", "candidate_replay", "release_schema"},
        ),
        (["tests/e2e/test_worker_otlp.py"], {"tests", "observability"}),
        (["scripts/ci_role_smoke.py"], {"tests", "packaging"}),
        (["scripts/ci_installation_roles.py"], {"tests", "packaging"}),
        (["tests/test_ci_installation_roles.py"], {"tests", "packaging"}),
        (["scripts/capture_release_schema.py"], {"upgrade", "release_schema"}),
        (["scripts/probe_seekdb_migration_lock.py"], {"seekdb_lock"}),
        (["tests/seekdb_support.py"], {"tests", "database", "seekdb_lock"}),
        (["tests/test_seekdb_migration_probe.py"], {"tests", "database", "upgrade", "seekdb_lock"}),
        (["tests/e2e/test_seekdb_restart_recovery.py"], {"tests", "database", "seekdb_lock"}),
        (
            ["tests/builtin/persistence/test_mysql_migration_runner.py"],
            {"tests", "database", "upgrade", "candidate_replay", "seekdb_lock"},
        ),
        (["tests/test_database_migration_cli.py"], {"tests", "database", "upgrade"}),
        ([".github/workflows/skill-guidance.yml"], {"skill_guidance"}),
        ([".licenserc.yaml"], set()),
        (["integrations/bub/src/powercontext_bub/__init__.py"], {"tests", "harness"}),
        (
            ["docs/en/docs/quickstart.md", "integrations/opencode/package.json"],
            {"website", "docs_tests", "tests", "opencode"},
        ),
        (
            ["scripts/guidance_templates/base/SKILL.md.j2"],
            {"tests", "dify", "hermes", "dsh", "pi", "opencode", "openclaw", "zcode", "kimi"},
        ),
    ],
)
def test_pr_selects_affected_suites(paths: list[str], expected: set[str]) -> None:
    selected = select_suites(paths, RULES)
    assert {suite for suite, enabled in selected.items() if enabled} == expected


@pytest.mark.parametrize(
    "path",
    [
        ".github/actions/setup-python-env/action.yml",
        ".github/workflows/ci-changes.yml",
        ".github/workflows/master.yml",
        ".github/ci-paths.toml",
        "tests/e2e/conftest.py",
        "new-component/code.py",
    ],
)
def test_shared_or_unclassified_changes_run_every_suite(path: str) -> None:
    assert all(select_suites(["docs/en/docs/quickstart.md", path], RULES).values())


@pytest.mark.parametrize(
    "path",
    [
        "src/powercontext/server/authz/owner_recovery.py",
        "src/powercontext/builtin/persistence/mysql.py",
        "uv.lock",
        "pyproject.toml",
    ],
)
def test_shared_runtime_inputs_keep_backend_upgrade_and_consumer_coverage(path: str) -> None:
    selected = select_suites([path], RULES)
    assert {
        "tests",
        "database",
        "upgrade",
        "observability",
        "packaging",
        "native",
        "desktop",
        "pi",
        "zcode",
        "cindy",
        "managed_resources",
    } <= {suite for suite, enabled in selected.items() if enabled}
    assert not selected["release_schema"]
    assert selected["seekdb_lock"] is (path != "src/powercontext/server/authz/owner_recovery.py")


def git(repository: Path, *arguments: str) -> str:
    return subprocess.check_output(["git", "-C", str(repository), *arguments], text=True).strip()  # noqa: S607


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    for relative in ("scripts/ci_changes.py", ".github/ci-paths.toml"):
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, destination)
    git(tmp_path, "init", "--initial-branch=master")
    git(tmp_path, "config", "user.name", "CI scope test")
    git(tmp_path, "config", "user.email", "ci-scope@example.invalid")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-m", "Initial")
    return tmp_path


def commit(repository: Path, *paths: str) -> str:
    for relative in paths:
        destination = repository / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text("test content\n", encoding="utf-8")
    git(repository, "add", "--all")
    git(repository, "commit", "-m", "Change files")
    return git(repository, "rev-parse", "HEAD")


def run_selector(repository: Path, event: str, base: str = "", head: str = "") -> dict[str, bool]:
    output = repository / "outputs.txt"
    output.unlink(missing_ok=True)
    subprocess.run(
        [
            sys.executable,
            str(repository / "scripts/ci_changes.py"),
            "--event-name",
            event,
            "--base",
            base,
            "--head",
            head,
        ],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "GITHUB_OUTPUT": str(output), "GITHUB_STEP_SUMMARY": str(repository / "summary.md")},
    )
    outputs = dict(line.split("=", 1) for line in output.read_text(encoding="utf-8").splitlines())
    return json.loads(outputs["suites"])


@pytest.mark.parametrize(
    ("path", "required_jobs"),
    [
        ("scripts/run_atomic_memory_clean_migration.py", {"seekdb-managed-startup", "upgrade-acceptance"}),
        (".github/actions/cache-pnpm/action.yml", {"tests", "windows-unit-portability", "cindy-package"}),
        ("deploy/helm/powercontext/values.yaml", {"helm"}),
        ("scripts/produce_managed_resources.py", {"managed-resource-candidates"}),
    ],
)
def test_changed_shared_input_requires_its_consumers_in_result_gate(repository, path, required_jobs):
    base = git(repository, "rev-parse", "HEAD")
    head = commit(repository, path)
    run_selector(repository, "pull_request", base, head)
    outputs = dict(line.split("=", 1) for line in (repository / "outputs.txt").read_text().splitlines())
    assert required_jobs <= set(json.loads(outputs["required-jobs"]))


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("evaluation/memory/locomo/dataset.py", {"tests", "evaluation"}),
        ("evaluation/memory/locomo_plus/metrics.py", {"tests", "evaluation"}),
        ("evaluation/memory/write_gate/runner.py", {"tests", "evaluation"}),
        ("integrations/pi/plugins/powercontext/package.json", {"tests", "pi", "zcode"}),
        ("tests/e2e/test_cross_host_work_continuity.py", {"tests", "zcode"}),
        ("tests/e2e/cross_host_work_continuity/pi.py", {"tests", "zcode"}),
        ("tests/e2e/cross_host_work_continuity/fixtures/task.py", {"tests", "zcode"}),
    ],
)
def test_legacy_evaluation_and_cross_host_changes_select_behavioral_suites(
    repository: Path, path: str, expected: set[str]
) -> None:
    base = git(repository, "rev-parse", "HEAD")
    head = commit(repository, path)

    selected = run_selector(repository, "pull_request", base, head)

    assert {suite for suite, enabled in selected.items() if enabled} == expected


def test_harness_workflow_change_selects_acceptance_and_database_suites(repository: Path) -> None:
    base = git(repository, "rev-parse", "HEAD")
    head = commit(repository, ".github/workflows/e2e-harness.yml")

    selected = run_selector(repository, "pull_request", base, head)

    assert {suite for suite, enabled in selected.items() if enabled} == {"harness", "database"}


def test_diff_covers_all_pr_commits_and_excludes_base_branch_changes(repository: Path) -> None:
    git(repository, "checkout", "-b", "feature")
    commit(repository, "integrations/pi/plugin.ts")
    head = commit(repository, "docs/zh/说明.md")
    git(repository, "checkout", "master")
    base = commit(repository, "src/powercontext/unrelated.py")
    git(repository, "checkout", "feature")

    selected = run_selector(repository, "pull_request", base, head)

    assert {suite for suite, enabled in selected.items() if enabled} == {
        "tests",
        "pi",
        "website",
        "docs_tests",
        "zcode",
    }


def test_moves_and_deletions_keep_the_original_suite(repository: Path) -> None:
    base = commit(repository, "integrations/pi/plugin.ts", "tests/native/test_service.py")
    (repository / "docs").mkdir()
    git(repository, "mv", "integrations/pi/plugin.ts", "docs/moved.md")
    git(repository, "rm", "tests/native/test_service.py")
    git(repository, "commit", "-m", "Move and delete")
    head = git(repository, "rev-parse", "HEAD")

    selected = run_selector(repository, "pull_request", base, head)

    assert {suite for suite, enabled in selected.items() if enabled} == {
        "tests",
        "pi",
        "website",
        "docs_tests",
        "native",
        "zcode",
    }


def test_large_diff_includes_code_after_hundreds_of_docs(repository: Path) -> None:
    base = git(repository, "rev-parse", "HEAD")
    head = commit(repository, *(f"docs/page-{index}.md" for index in range(350)), "src/powercontext/client.py")
    selected = run_selector(repository, "pull_request", base, head)
    assert selected["tests"] and selected["database"] and selected["upgrade"]
    assert selected["desktop"] and selected["website"]


@pytest.mark.parametrize("event", ["workflow_dispatch", "merge_group"])
def test_non_pr_events_run_every_suite(repository: Path, event: str) -> None:
    head = commit(repository, "docs/README.md")
    assert all(run_selector(repository, event, head, head).values())


def test_nightly_keeps_full_acceptance_without_archive_generation_or_lock_experiments(repository: Path) -> None:
    head = commit(repository, "docs/README.md")
    selected = run_selector(repository, "schedule", head, head)
    assert {suite for suite, enabled in selected.items() if not enabled} == {"release_schema", "seekdb_lock"}


@pytest.mark.parametrize("base", ["", "not-a-commit", "0" * 40])
def test_unavailable_diff_runs_every_suite(repository: Path, base: str) -> None:
    assert all(run_selector(repository, "pull_request", base, git(repository, "rev-parse", "HEAD")).values())


def test_push_uses_two_dot_range_from_the_verified_baseline(repository: Path) -> None:
    base = git(repository, "rev-parse", "HEAD")
    commit(repository, "scripts/upgrade_release_gate.py")
    head = commit(repository, "docs/zh/说明.md")

    selected = run_selector(repository, "push", base, head)

    assert {suite for suite, enabled in selected.items() if enabled} == {"upgrade", "website", "docs_tests"}


def test_rewritten_push_baseline_runs_every_suite(repository: Path) -> None:
    initial = git(repository, "rev-parse", "HEAD")
    old_head = commit(repository, "integrations/pi/index.ts")
    git(repository, "checkout", "-b", "rewritten", initial)
    head = commit(repository, "docs/new.md")

    assert all(run_selector(repository, "push", old_head, head).values())


def test_successful_push_baseline_retains_changes_from_cancelled_runs(repository: Path, monkeypatch) -> None:
    base = git(repository, "rev-parse", "HEAD")
    # The migration commit has no successful run; the docs push must still test it.
    commit(repository, "scripts/upgrade_release_gate.py")
    head = commit(repository, "docs/new.md")
    real_run = subprocess.run

    def run(command, **kwargs):
        if command[0] == "gh":
            return subprocess.CompletedProcess(command, 0, json.dumps({"workflow_runs": [{"head_sha": base}]}))
        return real_run(command, **kwargs)

    monkeypatch.setattr(ci_changes, "ROOT", repository)
    monkeypatch.setattr(subprocess, "run", run)
    output = repository / "outputs.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "ci_changes.py",
            "--event-name",
            "push",
            "--base",
            "",
            "--head",
            head,
            "--baseline-workflow",
            "master.yml",
            "--repository",
            "example/project",
            "--branch",
            "master",
        ],
    )

    ci_changes.main()

    outputs = dict(line.split("=", 1) for line in output.read_text().splitlines())
    selected = json.loads(outputs["suites"])
    assert {suite for suite, enabled in selected.items() if enabled} == {"upgrade", "website", "docs_tests"}


def test_no_successful_push_baseline_runs_every_suite(repository: Path) -> None:
    head = commit(repository, "docs/new.md")
    assert all(run_selector(repository, "push", "", head).values())


def test_successful_baseline_ignores_a_newer_success_on_rewritten_history(repository: Path, monkeypatch) -> None:
    base = git(repository, "rev-parse", "HEAD")
    unrelated = commit(repository, "integrations/pi/index.ts")
    git(repository, "checkout", "-b", "rewritten", base)
    head = commit(repository, "docs/new.md")
    real_run = subprocess.run

    def run(command, **kwargs):
        if command[0] == "gh":
            history = {"workflow_runs": [{"head_sha": unrelated}, {"head_sha": base}]}
            return subprocess.CompletedProcess(command, 0, json.dumps(history))
        return real_run(command, **kwargs)

    monkeypatch.setattr(ci_changes, "ROOT", repository)
    monkeypatch.setattr(subprocess, "run", run)

    assert ci_changes.successful_baseline("master.yml", "example/project", "master", head) == base


def test_selector_cli_exposes_full_matrix_and_required_jobs_on_missing_baseline(repository: Path) -> None:
    run_selector(repository, "pull_request", "", git(repository, "rev-parse", "HEAD"))
    outputs = dict(line.split("=", 1) for line in (repository / "outputs.txt").read_text().splitlines())
    matrix = json.loads(outputs["test-matrix"])
    assert set(matrix["python-version"]) == {"3.11", "3.12", "3.13", "3.14"}
    assert set(matrix["suite"]) == {"unit", "e2e"}
    assert set(json.loads(outputs["required-jobs"])) == {job for jobs in RULES["checks"].values() for job in jobs} | {
        "quality",
        "upgrade-release-gate",
    }
