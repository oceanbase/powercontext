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

import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path
from types import ModuleType

import pytest
import yaml


def _load_script(name: str) -> ModuleType:
    path = Path(__file__).parents[1] / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


smoke = _load_script("ci_release_smoke")


@pytest.fixture
def run_release_step(tmp_path: Path):
    """Run the workflow's version checks without building or publishing packages."""
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash is required to execute release workflow steps")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    uvx = bin_dir / "uvx"
    uvx.write_text(f'#!{sys.executable}\nimport os\nprint(os.environ["TEST_PACKAGE_VERSION"])\n')
    uvx.chmod(0o755)
    output = tmp_path / "output"

    def run(workflow_name: str, tag: str, package_version: str):
        output.write_text("")
        workflow = yaml.safe_load(
            (Path(__file__).parents[1] / ".github" / "workflows" / workflow_name).read_text(encoding="utf-8")
        )
        if workflow_name == "release.yml":
            # Follow the publishing workflow's real call into shared acceptance.
            # Running a copied check would miss drift in the release entry point.
            build = workflow["jobs"]["release-build"]
            assert build["with"]["release"] is True
            workflow = yaml.safe_load((Path(__file__).parents[1] / build["uses"]).read_text(encoding="utf-8"))
            job, step_name = "prepare", "Verify package version from Release tag"
        else:
            job, step_name = "verify", "Resolve release metadata"
        command = next(step["run"] for step in workflow["jobs"][job]["steps"] if step.get("name") == step_name)
        result = subprocess.run(
            [bash, "-eu", "-c", command],
            env={
                **os.environ,
                "PATH": os.pathsep.join((str(bin_dir), str(Path(sys.executable).parent), os.environ.get("PATH", ""))),
                "RELEASE_TAG": tag,
                "RELEASE_PACKAGE": "powercontext",
                "TEST_PACKAGE_VERSION": package_version,
                "GITHUB_OUTPUT": str(output),
            },
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        return result, output.read_text()

    return run


@pytest.mark.parametrize("workflow", ["release.yml", "release-verify.yml"])
@pytest.mark.parametrize(
    ("tag", "version"),
    [
        ("powercontext-v1.0.0rc1", "1.0.0rc1"),
        ("powercontext-v1.0.0rc2", "1.0.0rc2"),
        ("v1.0.0b2", "1.0.0b2"),
        ("1.0.0a1", "1.0.0a1"),
        ("powercontext-v1.0.0", "1.0.0"),
        ("powercontext-v1.1.0", "1.1.0"),
        ("v0.0.2", "0.0.2"),
    ],
)
def test_release_workflows_accept_python_release_versions(run_release_step, workflow, tag, version) -> None:
    result, output = run_release_step(workflow, tag, version)
    assert result.returncode == 0, result.stderr
    if workflow == "release-verify.yml":
        assert dict(line.split("=", 1) for line in output.splitlines()) == {
            "package": "powercontext",
            "version": version,
            "wheel": f"powercontext-{version}-py3-none-any.whl",
            "sdist": f"powercontext-{version}.tar.gz",
        }


@pytest.mark.parametrize("workflow", ["release.yml", "release-verify.yml"])
@pytest.mark.parametrize("version", ["1.0.0-rc.1", "1.0.0+local", "1.0.0.dev1", "1.0.0rc"])
def test_release_workflows_reject_unsupported_versions(run_release_step, workflow, version) -> None:
    result, output = run_release_step(workflow, f"powercontext-v{version}", version)
    assert result.returncode != 0
    assert "Release tag must use" in result.stderr or "release_tag must use" in result.stderr
    assert output == ""


def test_release_workflow_rejects_vcs_version_mismatch(run_release_step) -> None:
    result, _ = run_release_step("release.yml", "powercontext-v1.0.0rc1", "1.0.0rc2")
    assert result.returncode != 0
    assert "does not match VCS version" in result.stderr


@pytest.fixture
def resolve_publication(tmp_path, monkeypatch):
    """Execute the real metadata step against a local GitHub response fixture."""
    workflow = yaml.safe_load((Path(__file__).parents[1] / ".github/workflows/release.yml").read_text())
    command = workflow["jobs"]["resolve-release"]["steps"][0]["run"]
    source = command.removeprefix("python - <<'PY'\n").removesuffix("PY\n")
    tag = "powercontext-v1.4.1"
    sha = "a" * 40
    release = {"tag_name": tag, "draft": False, "published_at": "2026-10-26T10:00:00Z", "body": "Release notes"}
    output = tmp_path / "output"
    for key, value in {
        "RELEASE_TAG": tag,
        "SKIP_UPGRADE_ACCEPTANCE": "false",
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_REF": "refs/heads/master",
        "DEFAULT_BRANCH": "master",
        "GITHUB_REPOSITORY": "oceanbase/powercontext",
        "GITHUB_SHA": sha,
        "GH_TOKEN": "fixture-token",
        "RUNNER_TEMP": str(tmp_path),
        "GITHUB_OUTPUT": str(output),
    }.items():
        monkeypatch.setenv(key, value)

    def response(request, **_kwargs):
        if "/releases/tags/" in request.full_url:
            payload = release
        else:
            assert request.full_url.endswith("/commits/refs%2Ftags%2Fpowercontext-v1.4.1")
            payload = {"sha": sha}
        return io.BytesIO(json.dumps(payload).encode())

    monkeypatch.setattr(urllib.request, "urlopen", response)

    def run():
        exec(compile(source, "release.yml:resolve-release", "exec"), {})  # noqa: S102 -- checked-in workflow code
        return dict(line.split("=", 1) for line in output.read_text().splitlines())

    return run, release, tmp_path


def test_manual_release_pins_tag_commit_and_freezes_current_notes(resolve_publication):
    run, release, root = resolve_publication
    assert run() == {"tag": release["tag_name"], "source_sha": "a" * 40}
    assert json.loads((root / "release-metadata/event.json").read_text())["release"] == release


def test_upgrade_bypass_requires_explicit_new_database_notice(resolve_publication, monkeypatch):
    run, release, root = resolve_publication
    monkeypatch.setenv("SKIP_UPGRADE_ACCEPTANCE", "true")
    with pytest.raises(SystemExit, match="Add this exact notice"):
        run()
    assert not (root / "output").exists()
    release["body"] += (
        "\nUpgrade acceptance was skipped for this release. "
        "Use only new databases; upgrades of existing databases are not supported."
    )
    assert run()["source_sha"] == "a" * 40


@pytest.mark.parametrize("invalid", ["automatic-bypass", "branch", "tag", "draft", "moved-tag"])
def test_release_rejects_unintended_publication_target(resolve_publication, monkeypatch, invalid):
    run, release, root = resolve_publication
    if invalid == "automatic-bypass":
        monkeypatch.setenv("GITHUB_EVENT_NAME", "release")
        monkeypatch.setenv("SKIP_UPGRADE_ACCEPTANCE", "true")
    elif invalid == "branch":
        monkeypatch.setenv("GITHUB_REF", "refs/heads/feature")
    elif invalid == "tag":
        release["tag_name"] = "powercontext-v1.4.0"
    elif invalid == "draft":
        release["draft"] = True
    else:
        monkeypatch.setenv("GITHUB_EVENT_NAME", "release")
        monkeypatch.setenv("GITHUB_SHA", "b" * 40)
    with pytest.raises(SystemExit):
        run()
    assert not (root / "output").exists()


@pytest.mark.skipif(smoke.os.name == "nt", reason="POSIX venv symlink regression")
def test_release_smoke_resolves_console_script_from_verification_python(tmp_path) -> None:
    scripts = tmp_path / "verification" / "bin"
    scripts.mkdir(parents=True)
    base_python = tmp_path / "base-python"
    base_python.touch()
    python = scripts / "python"
    python.symlink_to(base_python)
    console_script = scripts / "powercontext"
    console_script.touch()

    assert smoke._console_script(python) == console_script
