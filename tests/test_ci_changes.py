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
        (["tests/builtin/persistence/test_mysql_schema.py"], {"tests", "database"}),
        (["tests/native/test_personal_service_lifecycle.py"], {"tests", "native"}),
        (["e2e/bub/compose.yaml"], {"harness"}),
        (["scripts/scan_e2e_evidence.sh"], {"tests", "harness"}),
        (["scripts/run_atomic_memory_clean_migration.py"], {"tests", "database"}),
        (["scripts/check_atomic_memory_migration_shards.py"], {"tests", "database"}),
        (["scripts/sync_dsh_plugin_tree.py"], {"tests", "dsh"}),
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
        "src/powercontext/client.py",
        "pyproject.toml",
        "uv.lock",
        "openapi/powercontext.yaml",
        ".github/actions/setup-python-env/action.yml",
        ".github/workflows/master.yml",
        ".github/ci-paths.toml",
        "tests/e2e/conftest.py",
        "new-component/code.py",
    ],
)
def test_shared_or_unclassified_changes_run_every_suite(path: str) -> None:
    assert all(select_suites(["docs/en/docs/quickstart.md", path], RULES).values())


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
    return json.loads(output.read_text(encoding="utf-8").removeprefix("suites="))


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
    assert all(run_selector(repository, "pull_request", base, head).values())


@pytest.mark.parametrize("event", ["push", "workflow_dispatch", "schedule"])
def test_non_pr_events_run_every_suite(repository: Path, event: str) -> None:
    head = commit(repository, "docs/README.md")
    assert all(run_selector(repository, event, head, head).values())


@pytest.mark.parametrize("base", ["", "not-a-commit", "0" * 40])
def test_unavailable_diff_runs_every_suite(repository: Path, base: str) -> None:
    assert all(run_selector(repository, "pull_request", base, git(repository, "rev-parse", "HEAD")).values())
