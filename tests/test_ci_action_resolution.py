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

# ruff: noqa: S607 - exercise checked-in composite steps with the runner's bash

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parents[1]
LOCAL_ACTION = "./.github/actions/"
REMOTE_ACTIONS = ("$/.github/actions/", "oceanbase/powercontext/.github/actions/")


def workflow_actions(path: Path) -> list[str]:
    workflow = yaml.safe_load(path.read_text())
    return [step["uses"] for job in workflow["jobs"].values() for step in job.get("steps", []) if "uses" in step]


def self_action_path(reference: str) -> Path | None:
    for prefix in (LOCAL_ACTION, *REMOTE_ACTIONS):
        if reference.startswith(prefix):
            return ROOT / ".github/actions" / reference.removeprefix(prefix).split("@", 1)[0] / "action.yml"
    return None


def action_closure(references: list[str]) -> list[tuple[str, str]]:
    pending = [("workflow", reference) for reference in references]
    visited = set()
    result = []
    while pending:
        caller, reference = pending.pop()
        path = self_action_path(reference)
        if path is None:
            continue
        result.append((caller, reference))
        if path in visited:
            continue
        visited.add(path)
        action = yaml.safe_load(path.read_text())
        pending.extend(
            (str(path.relative_to(ROOT)), step["uses"]) for step in action["runs"]["steps"] if "uses" in step
        )
    return result


@pytest.mark.parametrize(
    "workflow",
    sorted(path for path in (ROOT / ".github/workflows").glob("*.yml") if path.name != "build-artifacts.yml"),
    ids=lambda path: path.name,
)
def test_ordinary_ci_does_not_download_self_repository_for_actions(workflow: Path) -> None:
    remote = [
        f"{caller} -> {reference}"
        for caller, reference in action_closure(workflow_actions(workflow))
        if reference.startswith(REMOTE_ACTIONS)
    ]
    assert not remote, "Remote self actions download the repository again:\n" + "\n".join(remote)


def test_old_release_build_helpers_do_not_depend_on_checkout_actions() -> None:
    references = workflow_actions(ROOT / ".github/workflows/build-artifacts.yml")
    closure = action_closure(references)
    assert closure, "Release builds must select their helpers from the workflow revision"
    local = [f"{caller} -> {reference}" for caller, reference in closure if reference.startswith(LOCAL_ACTION)]
    assert not local, "Old release checkouts may lack workflow helpers:\n" + "\n".join(local)


@pytest.mark.parametrize(
    "helper", ["cache", "cache-restore", "cache-save", "setup-uv", "cache-pip", "setup-bundle-builder"]
)
def test_remote_cache_backend_runs_its_own_tool_with_an_old_checkout(tmp_path: Path, helper: str) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "ci_cache.py").write_text("raise RuntimeError('old checkout cache tool must not run')\n")
    action_path = ROOT / ".github/actions" / helper
    action = yaml.safe_load((action_path / "action.yml").read_text())
    backend = next((step for step in action["runs"]["steps"] if step.get("id") == "backend"), {})
    assert "run" in backend, "The workflow helper must select its backend without fetching another self action"
    output = tmp_path / "output"
    environment = tmp_path / "environment"
    env = dict(os.environ, GITHUB_ACTION_PATH=str(action_path), GITHUB_OUTPUT=str(output), GITHUB_ENV=str(environment))
    env.update(RUNNER_OS="Linux", RUNNER_ENVIRONMENT="github-hosted")
    result = subprocess.run(["bash", "-e", "-c", backend["run"]], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert output.read_text() == "backend=github\n"
    assert not environment.exists()


def test_remote_pip_cache_fingerprints_the_checkout_with_its_own_tool(tmp_path: Path) -> None:
    pytest.importorskip("pip")
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "ci_cache.py").write_text("raise RuntimeError('old checkout cache tool must not run')\n")
    requirements = tmp_path / "requirements.lock"
    requirements.write_text("fixture==1.0 --hash=sha256:abc\n")
    action_path = ROOT / ".github/actions/cache-pip"
    action = yaml.safe_load((action_path / "action.yml").read_text())
    snapshot = next(step for step in action["runs"]["steps"] if step.get("id") == "snapshot")
    output = tmp_path / "output"
    env = dict(os.environ, GITHUB_ACTION_PATH=str(action_path), GITHUB_OUTPUT=str(output))
    env.update(
        CI_CACHE_PYTHON=sys.executable,
        CI_CACHE_DEPENDENCY_PATH="requirements.lock",
        PIP_CACHE_DIR=str(tmp_path / "pip-cache"),
    )

    def fingerprint() -> str:
        output.write_text("")
        result = subprocess.run(
            ["bash", "-e", "-c", snapshot["run"]], cwd=tmp_path, env=env, capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr
        return next(line for line in output.read_text().splitlines() if line.startswith("cache-key="))

    original = fingerprint()
    requirements.write_text("fixture==2.0 --hash=sha256:def\n")
    assert fingerprint() != original


def test_remote_builder_uses_workflow_tool_pins_and_writes_to_old_checkout(tmp_path: Path) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "prepare_bundle_dependencies.py").write_text("raise RuntimeError('old packaging tool must not run')\n")
    (scripts / "managed_resource_build_tools.json").write_text("old packaging pins must not be used")
    action_path = ROOT / ".github/actions/setup-bundle-builder"
    action = yaml.safe_load((action_path / "action.yml").read_text())
    plan = next(step for step in action["runs"]["steps"] if step.get("id") == "plan")
    output = tmp_path / "output"
    work = "build/artifact-input"
    env = dict(os.environ, GITHUB_ACTION_PATH=str(action_path), GITHUB_OUTPUT=str(output), BUNDLE_WORK=work)
    result = subprocess.run(["bash", "-e", "-c", plan["run"]], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / work / "build-tools.json").read_bytes() == (
        ROOT / "scripts/managed_resource_build_tools.json"
    ).read_bytes()
    cache_path = str((tmp_path / work / "build-tool-wheels").resolve())
    assert json.loads(result.stdout)["cache-path"] == cache_path
    assert f"cache-path={cache_path}" in output.read_text()
