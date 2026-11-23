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
sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
selector = _load_script("ci_select_release_assets")


@pytest.fixture
def run_release_step(tmp_path: Path):
    """Run release steps with recorded package-tool commands, without downloads."""
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash is required to execute release workflow steps")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    package_tool = f"""#!{sys.executable}
import json
import os
import sys
from pathlib import Path

with open(os.environ["TEST_COMMAND_LOG"], "a") as output:
    output.write(json.dumps([Path(sys.argv[0]).name, *sys.argv[1:]]) + "\\n")
if sys.argv[-2:] == ["hatchling", "version"] or sys.argv[1:2] == ["-c"]:
    print(os.environ["TEST_PACKAGE_VERSION"])
elif sys.argv[1:2] == ["build"]:
    dist = Path("dist")
    dist.mkdir(exist_ok=True)
    (dist / "powercontext-1.5.2-py3-none-any.whl").touch()
    (dist / "powercontext-1.5.2.tar.gz").touch()
elif sys.argv[1:2] == ["venv"]:
    python = Path(sys.argv[2]) / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.symlink_to(Path(sys.argv[0]))
"""
    for name in ("uv", "uvx"):
        executable = bin_dir / name
        executable.write_text(package_tool)
        executable.chmod(0o755)
    output = tmp_path / "output"

    def run(workflow_name: str, tag: str, package_version: str, *, with_installer=True, step_name=None):
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
            job, step_name = "prepare", step_name or "Verify package version from Release tag"
        else:
            job, step_name = "verify", "Resolve release metadata"
        command = next(step["run"] for step in workflow["jobs"][job]["steps"] if step.get("name") == step_name)
        environment = {
            **os.environ,
            "PATH": os.pathsep.join((str(bin_dir), str(Path(sys.executable).parent), os.environ.get("PATH", ""))),
            "PC_CI_PYTHON_INSTALLER": str(Path(__file__).parents[1] / "scripts/ci_python_mirror.py"),
            "PC_CI_PYTHON_MIRROR": "",
            "PORTABLE_WHEEL": "false",
            "RELEASE_TAG": tag,
            "RELEASE_PACKAGE": "powercontext",
            "TEST_PACKAGE_VERSION": package_version,
            "TEST_COMMAND_LOG": str(tmp_path / "commands.jsonl"),
            "RUNNER_TEMP": str(tmp_path),
            "RUNNER_ENVIRONMENT": "github-hosted",
            "GITHUB_OUTPUT": str(output),
        }
        if not with_installer:
            environment.pop("PC_CI_PYTHON_INSTALLER")
        if workflow_name == "release-verify.yml":
            metadata = tmp_path / "release.json"
            metadata.write_text(json.dumps({"assets": [{"name": f"powercontext-{package_version}-py3-none-any.whl"}]}))
            args = [
                sys.executable,
                str(Path(__file__).parents[1] / "scripts/ci_select_release_assets.py"),
                "--metadata",
                str(metadata),
            ]
            assert command == "python scripts/ci_select_release_assets.py"
        elif os.name == "nt":
            if step_name != "Verify package version from Release tag":
                pytest.skip("release build shell fixtures require POSIX executable scripts")
            source = command.removeprefix("python - <<'PY'\n").removesuffix("PY\n")
            wrapper = (
                "import os; from unittest.mock import patch\n"
                "with patch('subprocess.check_output', return_value=os.environ['TEST_PACKAGE_VERSION']):\n"
                f"    exec(compile({source!r}, 'upgrade-release.yml:version', 'exec'))\n"
            )
            args = [sys.executable, "-c", wrapper]
        else:
            args = [bash, "-eu", "-c", command]
        result = subprocess.run(
            args,
            env=environment,
            cwd=tmp_path,
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
            "native": "false",
        }


@pytest.mark.parametrize("workflow", ["release.yml", "release-verify.yml"])
@pytest.mark.parametrize("version", ["1.0.0-rc.1", "1.0.0+local", "1.0.0.dev1", "1.0.0rc"])
def test_release_workflows_reject_unsupported_versions(run_release_step, workflow, version) -> None:
    result, output = run_release_step(workflow, f"powercontext-v{version}", version)
    assert result.returncode != 0
    assert "Release tag must use" in result.stderr or "release_tag must use" in result.stderr
    assert output == ""


@pytest.mark.parametrize("with_installer", [False, True])
def test_release_workflow_rejects_vcs_version_mismatch(run_release_step, with_installer) -> None:
    result, _ = run_release_step("release.yml", "powercontext-v1.0.0rc1", "1.0.0rc2", with_installer=with_installer)
    assert result.returncode != 0
    assert "does not match VCS version" in result.stderr


def test_release_workflow_accepts_existing_tag_without_python_installer(run_release_step) -> None:
    result, _ = run_release_step("release.yml", "powercontext-v1.5.2", "1.5.2", with_installer=False)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("with_installer", [False, True])
def test_release_build_and_fresh_install_support_tagged_setup(run_release_step, tmp_path, with_installer) -> None:
    result, _ = run_release_step(
        "release.yml",
        "powercontext-v1.5.2",
        "1.5.2",
        with_installer=with_installer,
        step_name="Build release distributions",
    )
    assert result.returncode == 0, result.stderr
    validated = tmp_path / "release-without-upgrades" / "validated-dist"
    shutil.copytree(tmp_path / "dist", validated)
    result, _ = run_release_step(
        "release.yml",
        "powercontext-v1.5.2",
        "1.5.2",
        with_installer=with_installer,
        step_name="Verify a clean installation and new-database Server, MCP and Memory behavior",
    )
    assert result.returncode == 0, result.stderr
    commands = [json.loads(line) for line in (tmp_path / "commands.jsonl").read_text().splitlines()]
    assert ["uv", "build", "--out-dir", "dist"] in commands
    twine = (
        ["uv", "run", "--no-project", "--with", "twine>=7.0.0", "--"]
        if with_installer
        else ["uvx", "--from", "twine>=7.0.0"]
    )
    assert [
        *twine,
        "twine",
        "check",
        "dist/powercontext-1.5.2-py3-none-any.whl",
        "dist/powercontext-1.5.2.tar.gz",
    ] in commands
    python = str(tmp_path / "release-fresh-install" / "bin" / "python")
    wheel = str(validated / "powercontext-1.5.2-py3-none-any.whl")
    assert ["uv", "pip", "install", "--python", python, f"{wheel}[cli,server]"] in commands
    assert ["uv", "pip", "check", "--python", python] in commands
    assert ["python", "scripts/ci_release_smoke.py", "--version", "1.5.2"] in commands


@pytest.fixture
def resolve_publication(tmp_path, monkeypatch):
    """Execute the real metadata step against a local GitHub response fixture."""
    workflow = yaml.safe_load((Path(__file__).parents[1] / ".github/workflows/release.yml").read_text())
    command = workflow["jobs"]["resolve-release"]["steps"][0]["run"]
    source = command.removeprefix("python - <<'PY'\n").removesuffix("PY\n")
    tag = "powercontext-v1.4.1"
    sha = "a" * 40
    release = {
        "tag_name": tag,
        "draft": False,
        "prerelease": False,
        "published_at": "2026-10-26T10:00:00Z",
        "body": "Release notes",
    }
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
    assert run() == {"tag": release["tag_name"], "source_sha": "a" * 40, "prerelease": "false"}
    assert json.loads((root / "release-metadata/event.json").read_text())["release"] == release


def test_release_preserves_github_prerelease_for_npm_channel(resolve_publication):
    run, release, _ = resolve_publication
    release["prerelease"] = True
    assert run()["prerelease"] == "true"


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


def _release_assets(*names: str):
    return {"assets": [{"name": name} for name in names]}


def test_native_release_detection_selects_the_installable_platform_wheel():
    from packaging.tags import Tag

    linux = "powercontext-1.4.1-cp311-abi3-manylinux_2_28_x86_64.whl"
    release = _release_assets(
        linux,
        "powercontext-1.4.1-cp311-abi3-manylinux_2_28_aarch64.whl",
        "powercontext-1.4.1-cp311-abi3-win_amd64.whl",
        "powercontext-1.4.1-cp311-abi3-macosx_11_0_arm64.whl",
        "powercontext-1.4.0-py3-none-any.whl",
        "powercontext-1.4.1-not-a-wheel.whl",
    )
    result = selector.select_assets(release, "1.4.1", "auto", [Tag("cp311", "abi3", "manylinux_2_28_x86_64")])
    assert result == {
        "package": "powercontext",
        "version": "1.4.1",
        "wheel": linux,
        "sdist": "powercontext-1.4.1.tar.gz",
        "native": "true",
    }


def test_native_release_selection_follows_python_tag_preference():
    from packaging.tags import Tag

    native = "powercontext-1.4.1-cp311-abi3-manylinux_2_28_x86_64.whl"
    pure = "powercontext-1.4.1-py3-none-any.whl"
    result = selector.select_assets(
        _release_assets(pure, native),
        "1.4.1",
        "powercontext",
        [Tag("cp311", "abi3", "manylinux_2_28_x86_64"), Tag("py3", "none", "any")],
    )
    assert result["wheel"] == native


@pytest.mark.parametrize("package", ["powercontext", "powermem"])
def test_legacy_release_detection_accepts_pure_python_assets(package):
    from packaging.tags import Tag

    wheel = f"{package}-1.4.1-py3-none-any.whl"
    result = selector.select_assets(_release_assets(wheel), "1.4.1", "auto", [Tag("py3", "none", "any")])
    assert result["wheel"] == wheel
    assert result["package"] == package
    assert result["native"] == "false"


def test_release_detection_rejects_ambiguous_contract_and_incompatible_wheels():
    from packaging.tags import Tag

    with pytest.raises(SystemExit, match="uniquely detect"):
        selector.select_assets(
            _release_assets("powercontext-1.4.1-py3-none-any.whl", "powermem-1.4.1-py3-none-any.whl"),
            "1.4.1",
            "auto",
            [Tag("py3", "none", "any")],
        )
    with pytest.raises(SystemExit, match="compatible"):
        selector.select_assets(
            _release_assets("powercontext-1.4.1-cp311-abi3-win_amd64.whl"),
            "1.4.1",
            "auto",
            [Tag("cp311", "abi3", "manylinux_2_28_x86_64")],
        )


def test_release_publication_collects_platform_wheels_without_replacing_the_candidate():
    root = Path(__file__).parents[1]
    release = yaml.safe_load((root / ".github/workflows/release.yml").read_text(encoding="utf-8"))
    native_job = release["jobs"]["native-wheel-build"]
    native = yaml.safe_load((root / native_job["uses"]).read_text(encoding="utf-8"))
    entries = native["jobs"]["wheels"]["strategy"]["matrix"]["include"]
    assert {entry["platform"] for entry in entries} == {
        "manylinux-x86_64",
        "manylinux-aarch64",
        "macos-x86_64",
        "macos-arm64",
        "windows-x86_64",
    }
    build = next(
        step for step in native["jobs"]["wheels"]["steps"] if step.get("run", "").startswith("python -m cibuildwheel")
    )
    assert build["env"]["CIBW_BUILD"] == "cp31{1,2,3,4}-*"
    assert build["env"]["CIBW_MANYLINUX_X86_64_IMAGE"] == "manylinux_2_28"
    for job in ("pypi-publish", "release-assets"):
        assert "native-wheel-build" in release["jobs"][job]["needs"]
        steps = release["jobs"][job]["steps"]
        download = next(step["with"] for step in steps if step.get("with", {}).get("name") == "release-dists")
        assert download["path"] == "candidate-dist/"
        wheels = next(step["with"] for step in steps if "pattern" in step.get("with", {}))
        assert wheels["pattern"] == "native-wheels-*"
        assert not wheels.get("merge-multiple", False)
        assert wheels["path"] == "native-dist/"
        assert "native" in next(step["if"] for step in steps if step.get("with", {}).get("pattern"))
        stage = next(step["run"] for step in steps if step.get("name") == "Verify and stage accepted publication bytes")
        assert "scripts/ci_stage_release.py" in stage and "candidate-evidence/publication.json" in stage
        assert "skipped" in release["jobs"][job]["if"]
    assert "native" in native_job["if"]
    assert release["jobs"]["release-build"]["with"]["portable_wheel"] == (
        "${{ needs.resolve-release.outputs.native == 'true' }}"
    )


def test_release_selector_command_uses_current_python_tags(tmp_path, monkeypatch):
    from packaging.tags import sys_tags

    supported = next(tag for tag in sys_tags() if tag.interpreter == "cp311" and tag.abi == "abi3")
    wheel = f"powercontext-1.4.1-{supported}.whl"
    metadata = tmp_path / "release.json"
    metadata.write_text(json.dumps(_release_assets(wheel)), encoding="utf-8")
    output = tmp_path / "output"
    monkeypatch.setenv("RELEASE_TAG", "powercontext-v1.4.1")
    monkeypatch.setenv("RELEASE_PACKAGE", "auto")
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setattr(sys, "argv", ["ci_select_release_assets.py", "--metadata", str(metadata)])
    selector.main()
    selected = dict(line.split("=", 1) for line in output.read_text(encoding="utf-8").splitlines())
    assert selected["wheel"] == wheel
    assert selected["native"] == "true"
    assert selected["package"] == "powercontext"


@pytest.mark.parametrize(
    ("workflow_name", "job_name"),
    [
        ("build-artifacts.yml", "build"),
        ("desktop-candidate.yml", "contract"),
        ("desktop-candidate.yml", "native"),
        ("desktop-candidate.yml", "assemble"),
        ("managed-resource-candidates.yml", "candidate"),
        ("e2e-harness.yml", "validate"),
        ("desktop.yml", "windows"),
        ("desktop.yml", "macos"),
        ("desktop.yml", "linux"),
        ("desktop.yml", "installed-package"),
    ],
)
def test_source_installation_has_a_native_compiler_before_building(workflow_name, job_name):
    path = Path(__file__).parents[1] / ".github/workflows" / workflow_name
    steps = yaml.safe_load(path.read_text(encoding="utf-8"))["jobs"][job_name]["steps"]
    source_install = next(
        i
        for i, step in enumerate(steps)
        if any(
            f"{installer} {operation}" in step.get("run", "")
            for installer in ("uv", "scripts/ci_python_mirror.py", '"$PC_CI_PYTHON_INSTALLER"')
            for operation in ("sync", "build")
        )
    )
    assert any("rustup toolchain install" in step.get("run", "") for step in steps[:source_install])


@pytest.mark.parametrize("native", [False, True])
def test_release_routes_tagged_sources_to_their_supported_wheel_builder(tmp_path, native):
    workflow = yaml.safe_load((Path(__file__).parents[1] / ".github/workflows/release.yml").read_text(encoding="utf-8"))
    inspect = next(step for step in workflow["jobs"]["resolve-release"]["steps"] if step.get("id") == "build")
    command = inspect["run"].split("python - <<'PY'\n", 1)[1].removesuffix("PY\n")
    requires = ["hatchling", "hatch-vcs"]
    if native:
        requires.extend(["maturin>=1.16,<2", "typing-extensions>=4.12,<5"])
    (tmp_path / "tagged-pyproject.toml").write_text("[build-system]\nrequires = " + json.dumps(requires) + "\n")
    output = tmp_path / "output"
    result = subprocess.run(
        [sys.executable, "-c", command],
        env={**os.environ, "RUNNER_TEMP": str(tmp_path), "GITHUB_OUTPUT": str(output)},
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert output.read_text().strip() == f"native={str(native).lower()}"
    jobs = workflow["jobs"]
    assert jobs["native-wheel-build"]["if"] == "needs.resolve-release.outputs.native == 'true'"
    assert jobs["release-build"]["with"]["portable_wheel"] == "${{ needs.resolve-release.outputs.native == 'true' }}"
