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

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

SCRIPT = Path(__file__).parents[1] / "scripts" / "ci_cache.py"


def output_value(output: str, name: str) -> str:
    return next(line.split("=", 1)[1] for line in output.splitlines() if line.startswith(name + "="))


def run_cache(tmp_path: Path, mode: str, **values: str) -> tuple[subprocess.CompletedProcess[str], str, str]:
    output = tmp_path / "output"
    environment = tmp_path / "environment"
    output.write_text("")
    environment.write_text("")
    env = {key: value for key, value in os.environ.items() if not key.startswith(("PC_CI_CACHE_", "CI_CACHE_"))}
    env.update(GITHUB_OUTPUT=str(output), GITHUB_ENV=str(environment), RUNNER_OS="Linux", RUNNER_ARCH="X64")
    env.update(values)
    result = subprocess.run(
        [sys.executable, str(SCRIPT), mode], cwd=tmp_path, env=env, capture_output=True, text=True, check=False
    )
    return result, output.read_text(), environment.read_text()


def test_internal_cache_registers_namespaced_credentials_without_default_aws_environment(tmp_path: Path) -> None:
    result, output, environment = run_cache(
        tmp_path,
        "backend",
        RUNNER_ENVIRONMENT="self-hosted",
        PC_CI_CACHE_ENDPOINT="http://cache.example.test:8333",
        PC_CI_CACHE_BUCKET="ci-cache",
        PC_CI_CACHE_ACCESS_KEY="test-access",
        PC_CI_CACHE_SECRET_KEY="test-secret",  # noqa: S106 - inert test fixture
    )
    assert result.returncode == 0, result.stderr
    assert "backend=s3" in output
    assert "PC_CI_CACHE_SECRET_KEY=test-secret" in environment
    assert "AWS_ACCESS_KEY_ID" not in environment
    assert "AWS_SECRET_ACCESS_KEY" not in environment
    assert "test-secret" not in output
    assert "::add-mask::test-secret" in result.stdout


def test_hosted_runner_uses_github_even_when_process_has_internal_configuration(tmp_path: Path) -> None:
    result, output, environment = run_cache(
        tmp_path,
        "backend",
        RUNNER_ENVIRONMENT="github-hosted",
        PC_CI_CACHE_ENDPOINT="http://cache.example.test:8333",
    )
    assert result.returncode == 0, result.stderr
    assert "backend=github" in output
    assert environment == ""


def test_configured_internal_cache_does_not_fall_back_when_credentials_are_missing(tmp_path: Path) -> None:
    result, output, environment = run_cache(
        tmp_path,
        "backend",
        RUNNER_ENVIRONMENT="self-hosted",
        PC_CI_CACHE_ENDPOINT="http://cache.example.test:8333",
    )
    assert result.returncode != 0
    assert "PC_CI_CACHE_BUCKET" in result.stderr
    assert "backend=github" not in output
    assert environment == ""


def test_macos_keeps_original_cache_backend(tmp_path: Path) -> None:
    result, output, environment = run_cache(tmp_path, "backend", RUNNER_OS="macOS", RUNNER_ENVIRONMENT="self-hosted")
    assert result.returncode == 0, result.stderr
    assert "backend=github" in output
    assert environment == ""


@pytest.mark.parametrize(
    ("cache_input", "upstream_key", "save_input", "event", "expected"),
    [
        ("auto", "resolved-by-upstream", "auto", "pull_request", {"internal-cache"}),
        ("auto", "", "auto", "pull_request", set()),
        ("false", "", "auto", "pull_request", set()),
        ("true", "resolved-by-upstream", "false", "pull_request", {"internal-restore"}),
        ("true", "resolved-by-upstream", "auto", "merge_group", {"internal-restore"}),
    ],
)
def test_uv_cache_follows_upstream_cache_decision_and_save_boundary(
    cache_input: str, upstream_key: str, save_input: str, event: str, expected: set[str]
) -> None:
    action = yaml.safe_load((SCRIPT.parents[1] / ".github/actions/setup-uv/action.yml").read_text())
    context = {
        "steps.backend.outputs.backend": "s3",
        "steps.uv.outputs.cache-key": upstream_key,
        "inputs.enable-cache": cache_input,
        "inputs.save-cache": save_input,
        "inputs.restore-cache": "true",
        "github.event_name": event,
    }
    selected = set()
    for step in action["runs"]["steps"]:
        if step.get("id") not in {"internal-cache", "internal-restore"}:
            continue
        expression = step["if"]
        for name, value in context.items():
            expression = expression.replace(name, repr(value))
        # These checked-in conditions only use string comparisons and boolean
        # operators, whose semantics are the same in Python and Actions.
        expression = expression.replace("&&", " and ").replace("||", " or ")
        if eval(expression, {"__builtins__": {}}):  # noqa: S307 - trusted action condition with fixed test values
            selected.add(step["id"])
    assert selected == expected


def test_cargo_cache_key_tracks_source_lock_and_compiler_environment(tmp_path: Path) -> None:
    tools = tmp_path / "tools"
    tools.mkdir()
    rustc = tools / "rustc"
    rustc.write_text("#!/bin/sh\nprintf 'rustc 1.95.0\\nhost: x86_64-unknown-linux-gnu\\n'\n")
    rustc.chmod(0o755)
    workspace = tmp_path / "gateway"
    workspace.mkdir()
    (workspace / "Cargo.toml").write_text('[package]\nname="fixture"\nversion="1.0.0"\n')
    (workspace / "Cargo.lock").write_text("version = 4\n")
    (workspace / "lib.rs").write_text("pub fn value() -> u32 { 1 }\n")
    values = {"PATH": f"{tools}{os.pathsep}{os.environ['PATH']}", "CI_CACHE_WORKSPACES": "gateway -> target"}
    first, initial, _ = run_cache(tmp_path, "cargo", **values)
    assert first.returncode == 0, first.stderr
    (workspace / "lib.rs").write_text("pub fn value() -> u32 { 2 }\n")
    changed, source_changed, _ = run_cache(tmp_path, "cargo", **values)
    assert changed.returncode == 0, changed.stderr
    assert output_value(initial, "cache-key") != output_value(source_changed, "cache-key")
    assert output_value(initial, "restore-key") == output_value(source_changed, "restore-key")
    (workspace / "Cargo.lock").write_text("version = 4\n# new resolution\n")
    changed, lock_changed, _ = run_cache(tmp_path, "cargo", **values)
    assert changed.returncode == 0, changed.stderr
    assert output_value(source_changed, "restore-key") != output_value(lock_changed, "restore-key")
    changed, env_changed, _ = run_cache(tmp_path, "cargo", **values, RUSTFLAGS="-C opt-level=2")
    assert changed.returncode == 0, changed.stderr
    assert output_value(lock_changed, "restore-key") != output_value(env_changed, "restore-key")
    assert str(workspace / "target") in env_changed
    assert "cache-path" in env_changed


def test_mise_cache_key_tracks_tool_configuration(tmp_path: Path) -> None:
    (tmp_path / "mise.toml").write_text('[tools]\nnode = "22"\n')
    first, initial, _ = run_cache(tmp_path, "mise")
    assert first.returncode == 0, first.stderr
    (tmp_path / "mise.toml").write_text('[tools]\nnode = "24"\n')
    changed, updated, _ = run_cache(tmp_path, "mise")
    assert changed.returncode == 0, changed.stderr
    assert initial != updated


@pytest.mark.parametrize("changed_input", ["source", "included-fixture"])
def test_cargo_restore_rebuilds_workspace_inputs_and_preserves_dependencies(tmp_path: Path, changed_input: str) -> None:
    cargo = shutil.which("cargo")
    if cargo is None:
        pytest.skip("requires a Rust toolchain; also exercised in the Linux runner image")
    workspace = tmp_path / "gateway"
    app = workspace / "app"
    dependency = workspace / "dep"
    for directory in (app, dependency):
        (directory / "src").mkdir(parents=True)
    (workspace / "Cargo.toml").write_text('[workspace]\nmembers=["app"]\nexclude=["dep"]\nresolver="2"\n')
    (app / "Cargo.toml").write_text(
        '[package]\nname="cache-fixture-app"\nversion="0.1.0"\nedition="2021"\n'
        '[dependencies]\ncache-fixture-dep={path="../dep"}\n'
    )
    (dependency / "Cargo.toml").write_text('[package]\nname="cache-fixture-dep"\nversion="0.1.0"\n')
    (dependency / "src/lib.rs").write_text("pub fn value() -> u32 { 7 }\n")
    source = app / "src/main.rs"
    fixture = tmp_path / "fixture.txt"
    fixture.write_text("A")
    if changed_input == "included-fixture":
        source.write_text(
            'fn main() { println!("{} {}", include_str!("../../../fixture.txt"), cache_fixture_dep::value()); }\n'
        )
        changed_file = fixture
    else:
        source.write_text('fn main() { println!("A {}", cache_fixture_dep::value()); }\n')
        changed_file = source
    original_mtime = changed_file.stat().st_mtime_ns
    command = [cargo, "run", "--offline", "--quiet", "--manifest-path", str(workspace / "Cargo.toml")]
    first = subprocess.run(command, capture_output=True, text=True, check=True)
    assert first.stdout.strip() == "A 7"
    target = workspace / "target"
    snapshot = tmp_path / "snapshot"
    shutil.copytree(target, snapshot)
    dependency_artifact = next(target.glob("debug/deps/libcache_fixture_dep-*.rlib"))
    dependency_mtime = dependency_artifact.stat().st_mtime_ns
    changed_file.write_text(changed_file.read_text().replace("A", "B"))
    os.utime(changed_file, ns=(original_mtime, original_mtime))
    shutil.rmtree(target)
    shutil.copytree(snapshot, target, copy_function=shutil.copy2)
    # An archive can be newer than this job's checkout. Cargo's mtime check alone
    # can then accept a different PR's workspace binary as fresh, including an
    # exact key hit when an embedded fixture lives outside the Cargo workspace.
    stale = subprocess.run(command, capture_output=True, text=True, check=True)
    assert stale.stdout.strip() == "A 7"
    result, _, _ = run_cache(tmp_path, "cargo-clean", CI_CACHE_WORKSPACES="gateway -> target")
    assert result.returncode == 0, result.stderr
    rebuilt = subprocess.run(command, capture_output=True, text=True, check=True)
    assert rebuilt.stdout.strip() == "B 7"
    assert dependency_artifact.stat().st_mtime_ns == dependency_mtime
