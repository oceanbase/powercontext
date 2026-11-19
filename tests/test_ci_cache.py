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


@pytest.mark.parametrize(
    ("upper", "lower", "expected"),
    [
        ("localhost,.upper.test", "localhost,.lower.test", "localhost,.upper.test,.lower.test,cache.example.test"),
        ("localhost", "", "localhost,cache.example.test"),
        ("", ".lower.test", ".lower.test,cache.example.test"),
        ("", "", "cache.example.test"),
    ],
)
def test_internal_cache_bypasses_proxy_for_both_environment_spellings(
    tmp_path: Path, upper: str, lower: str, expected: str
) -> None:
    values = {
        "RUNNER_ENVIRONMENT": "self-hosted",
        "PC_CI_CACHE_ENDPOINT": "http://cache.example.test:8333",
        "PC_CI_CACHE_BUCKET": "ci-cache",
        "PC_CI_CACHE_ACCESS_KEY": "test-access",
        "PC_CI_CACHE_SECRET_KEY": "test-secret",
        "NO_PROXY": upper,
        "no_proxy": lower,
    }
    result, _, environment = run_cache(tmp_path, "backend", **values)
    assert result.returncode == 0, result.stderr
    assert output_value(environment, "NO_PROXY") == expected
    assert output_value(environment, "no_proxy") == expected
    assert "HTTP_PROXY=" not in environment
    assert "http_proxy=" not in environment
    # Every nested helper selects its backend. Repeating it must not grow the
    # bypass list or discard entries originally present in either spelling.
    values.update(NO_PROXY=expected, no_proxy=expected)
    result, _, repeated = run_cache(tmp_path, "backend", **values)
    assert result.returncode == 0, result.stderr
    assert output_value(repeated, "NO_PROXY") == expected
    assert output_value(repeated, "no_proxy") == expected


@pytest.mark.parametrize("name", ["cache", "cache-restore", "cache-save"])
def test_cache_action_environment_has_no_case_insensitive_duplicate_keys(name: str) -> None:
    action = yaml.safe_load((SCRIPT.parents[1] / f".github/actions/{name}/action.yml").read_text())
    for step in action["runs"]["steps"]:
        names = list(step.get("env", {}))
        # GitHub validates action YAML environment mappings without case
        # distinctions, even though a Linux process environment is case-sensitive.
        assert len(names) == len({key.casefold() for key in names}), step["id"]


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


@pytest.mark.parametrize("changed_input", ["package", "lock", "architecture"])
def test_npm_download_cache_tracks_install_inputs_and_excludes_installed_packages(
    tmp_path: Path, changed_input: str
) -> None:
    if shutil.which("npm") is None:
        pytest.skip("requires npm")
    lock = tmp_path / "package-lock.json"
    lock.write_text('{"lockfileVersion": 3, "packages": {}}')
    values = {
        "npm_config_cache": str(tmp_path / "npm-cache"),
        "CI_CACHE_PACKAGES": "@openai/codex@0.167.2",
        "CI_CACHE_DEPENDENCY_PATH": "package-lock.json",
    }
    first, initial, _ = run_cache(tmp_path, "npm", **values)
    assert first.returncode == 0, first.stderr
    assert output_value(initial, "cache-path") == str(tmp_path / "npm-cache/_cacache")
    if changed_input == "package":
        values["CI_CACHE_PACKAGES"] = "@openai/codex@0.168.0"
    elif changed_input == "lock":
        lock.write_text('{"lockfileVersion": 3, "packages": {"node_modules/bun": {"version": "1.4.4"}}}')
    else:
        values["RUNNER_ARCH"] = "ARM64"
    changed, updated, _ = run_cache(tmp_path, "npm", **values)
    assert changed.returncode == 0, changed.stderr
    assert output_value(initial, "cache-key") != output_value(updated, "cache-key")


def test_pip_download_cache_tracks_exported_dependencies_and_excludes_built_wheels(tmp_path: Path) -> None:
    pytest.importorskip("pip")
    requirements = tmp_path / "requirements.lock"
    requirements.write_text("fixture==1.0 --hash=sha256:abc\n")
    values = {"PIP_CACHE_DIR": str(tmp_path / "pip-cache"), "CI_CACHE_DEPENDENCY_PATH": "requirements.lock"}
    first, initial, _ = run_cache(tmp_path, "pip", **values)
    assert first.returncode == 0, first.stderr
    assert str(tmp_path / "pip-cache/http") in initial
    assert str(tmp_path / "pip-cache/http-v2") in initial
    assert "/wheels" not in initial
    requirements.write_text("fixture==2.0 --hash=sha256:def\n")
    changed, updated, _ = run_cache(tmp_path, "pip", **values)
    assert changed.returncode == 0, changed.stderr
    assert output_value(initial, "cache-key") != output_value(updated, "cache-key")
    changed, architecture, _ = run_cache(tmp_path, "pip", **values, RUNNER_ARCH="ARM64")
    assert changed.returncode == 0, changed.stderr
    assert output_value(updated, "cache-key") != output_value(architecture, "cache-key")


def test_restored_pip_http_cache_reuses_download_but_recreates_wheelhouse(tmp_path: Path) -> None:
    pytest.importorskip("pip")
    import hashlib
    import io
    import threading
    import zipfile
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    wheel_name = "cache_fixture-1.0-py3-none-any.whl"
    wheel = io.BytesIO()
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("cache_fixture/__init__.py", "value = 1\n")
        archive.writestr(
            "cache_fixture-1.0.dist-info/METADATA", "Metadata-Version: 2.1\nName: cache-fixture\nVersion: 1.0\n"
        )
        archive.writestr("cache_fixture-1.0.dist-info/WHEEL", "Wheel-Version: 1.0\nTag: py3-none-any\n")
        archive.writestr("cache_fixture-1.0.dist-info/RECORD", "")
    payload = wheel.getvalue()
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            requests.append(self.path)
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "public, max-age=3600")
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - BaseHTTPRequestHandler signature
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    requirements = tmp_path / "requirements.lock"
    requirements.write_text(
        f"http://127.0.0.1:{server.server_port}/{wheel_name} --hash=sha256:{hashlib.sha256(payload).hexdigest()}\n"
    )
    cache = tmp_path / "pip-cache"
    destination = tmp_path / "wheelhouse"
    env = {**os.environ, "PIP_CACHE_DIR": str(cache), "PIP_DISABLE_PIP_VERSION_CHECK": "1"}
    command = [
        sys.executable,
        "-m",
        "pip",
        "download",
        "--require-hashes",
        "--only-binary=:all:",
        "--no-deps",
        "--trusted-host",
        "127.0.0.1",
        "--no-index",
        "--dest",
        str(destination),
        "--requirement",
        str(requirements),
    ]
    try:
        subprocess.run(command, env=env, capture_output=True, text=True, check=True)
        assert requests == [f"/{wheel_name}"]
        result, identity, _ = run_cache(
            tmp_path, "pip", PIP_CACHE_DIR=str(cache), CI_CACHE_DEPENDENCY_PATH="requirements.lock"
        )
        assert result.returncode == 0, result.stderr
        snapshot = tmp_path / "snapshot"
        snapshot.mkdir()
        for name in ("http", "http-v2"):
            if (cache / name).exists():
                assert str(cache / name) in identity
                shutil.copytree(cache / name, snapshot / name)
        shutil.rmtree(cache)
        shutil.rmtree(destination)
        shutil.copytree(snapshot, cache)
        restored = subprocess.run(command, env=env, capture_output=True, text=True, check=True)
        assert requests == [f"/{wheel_name}"], restored.stdout
        assert (destination / wheel_name).read_bytes() == payload
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_tck_prepare_restores_pinned_sources_without_installed_runners(tmp_path: Path) -> None:
    import json

    git = shutil.which("git")
    if git is None:
        pytest.skip("requires git")
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    subprocess.run([git, "init", "--quiet", str(upstream)], check=True)
    (upstream / "suite.txt").write_text("pinned upstream suite\n")
    subprocess.run([git, "-C", str(upstream), "add", "suite.txt"], check=True)
    subprocess.run(
        [
            git,
            "-C",
            str(upstream),
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.test",
            "commit",
            "-qm",
            "pin",
        ],
        check=True,
    )
    commit = subprocess.check_output([git, "-C", str(upstream), "rev-parse", "HEAD"], text=True).strip()
    root = tmp_path / "checkout"
    script = root / "tests/e2e/gateway_tck.py"
    script.parent.mkdir(parents=True)
    shutil.copyfile(SCRIPT.parents[1] / "tests/e2e/gateway_tck.py", script)
    tck = root / "gateway/tck"
    tck.mkdir(parents=True)
    (tck / "upstreams.json").write_text(
        json.dumps({name: {"repository": str(upstream), "commit": commit} for name in ("openai_sdk", "openresponses")})
    )
    cache = root / ".cache/gateway-tck"
    command = [sys.executable, str(script), "--prepare-only", "--cache", str(cache)]
    first = subprocess.run(command, capture_output=True, text=True, check=False)
    assert first.returncode == 0, first.stderr
    shutil.rmtree(upstream)
    restored = subprocess.run(command, capture_output=True, text=True, check=False)
    assert restored.returncode == 0, restored.stderr
    assert sorted(path.name for path in cache.iterdir()) == [f"openai_sdk-{commit}", f"openresponses-{commit}"]
    assert not (tck / "node_modules").exists()
    (cache / f"openai_sdk-{commit}" / "suite.txt").write_text("changed suite\n")
    modified = subprocess.run(command, capture_output=True, text=True, check=False)
    assert modified.returncode != 0


def test_restored_npm_download_cache_reinstalls_packages_offline(tmp_path: Path) -> None:
    import functools
    import io
    import tarfile
    import threading
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

    npm = shutil.which("npm")
    if npm is None:
        pytest.skip("requires npm")
    package = b'{"name":"cache-fixture","version":"1.0.0"}'
    with tarfile.open(tmp_path / "fixture.tgz", "w:gz") as archive:
        metadata = tarfile.TarInfo("package/package.json")
        metadata.size = len(package)
        archive.addfile(metadata, io.BytesIO(package))
    server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(SimpleHTTPRequestHandler, directory=str(tmp_path)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    cache = tmp_path / "npm-cache"
    prefix = tmp_path / "install"
    url = f"http://127.0.0.1:{server.server_port}/fixture.tgz"
    env = {**os.environ, "npm_config_cache": str(cache)}
    command = [npm, "install", "--prefix", str(prefix), "--ignore-scripts", "--no-audit", "--no-fund", url]
    try:
        subprocess.run(command, env=env, capture_output=True, text=True, check=True)
    finally:
        server.shutdown()
        thread.join()
        server.server_close()
    result, identity, _ = run_cache(tmp_path, "npm", npm_config_cache=str(cache), CI_CACHE_PACKAGES=url)
    assert result.returncode == 0, result.stderr
    downloads = Path(output_value(identity, "cache-path"))
    snapshot = tmp_path / "snapshot"
    shutil.copytree(downloads, snapshot)
    shutil.rmtree(cache)
    shutil.rmtree(prefix)
    shutil.copytree(snapshot, downloads)
    # The upstream is offline and the entire previous installation is absent.
    subprocess.run([*command, "--offline"], env=env, capture_output=True, text=True, check=True)
    assert (prefix / "node_modules/cache-fixture/package.json").read_bytes() == package
