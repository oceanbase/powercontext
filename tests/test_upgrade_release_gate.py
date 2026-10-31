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

"""Release-blocking regressions, not evidence that any database upgrade works."""

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from urllib.parse import urlsplit
from xml.etree import ElementTree

import parallel_upgrade_release_gate as parallel
import pytest
import upgrade_release_gate as gate
import upgrade_release_matrix as matrix
import yaml
from ci_legacy_upgrade_rejection import database_environment

from powercontext.server.settings import ServerSettings
from tests.release_upgrades.test_full_server import environment_for


@pytest.fixture
def release(tmp_path, monkeypatch):
    # Disposable release fixtures own their checkout identity, independently of CI.
    monkeypatch.delenv("PC_UPGRADE_SOURCE_SHA", raising=False)
    monkeypatch.delenv("GITHUB_SHA", raising=False)
    persistence = tmp_path / "src/powercontext/builtin/persistence/tables.py"
    persistence.parent.mkdir(parents=True)
    persistence.write_text("# disposable persistence input\n")
    contract = tmp_path / "openapi/powercontext.yaml"
    contract.parent.mkdir()
    contract.write_text("info:\n  version: 2.0.0\n")
    (tmp_path / "release").mkdir()
    policy = {
        "format": 2,
        "target_version": "2.0.0",
        "mode": "upgrade-required",
        "schema_review": {"persistence_sha256": gate.managed_digest(tmp_path), "rationale": "No upgrades supported"},
        "cases": [],
        "automatic_sources": None,
    }
    suite = tmp_path / "tests/release_upgrades/test_example.py"
    suite.parent.mkdir(parents=True)
    suite.write_text("# Gate orchestration fixture; not real upgrade acceptance.\n")
    automatic_policy(policy)
    monkeypatch.setattr(gate, "run_case", lambda _root, _wheel, case, _output: {"case": case, "status": "passed"})
    (tmp_path / gate.MANIFEST).write_text(json.dumps(policy))
    monkeypatch.setattr(
        matrix,
        "fetch_catalog",
        lambda: {
            "releases": {
                "1.3.0": [
                    {
                        "filename": "powercontext-1.3.0-py3-none-any.whl",
                        "packagetype": "bdist_wheel",
                        "url": "https://files.pythonhosted.org/packages/test.whl",
                        "digests": {"sha256": "a" * 64},
                    }
                ]
            }
        },
    )
    for language in ("en", "zh"):
        page = tmp_path / f"docs/{language}/docs/get-started/install-and-run.md"
        page.parent.mkdir(parents=True)
        page.write_text("<!-- upgrade-support:start -->\nplaceholder\n<!-- upgrade-support:end -->\n")
    gate.check_docs(tmp_path, policy, write=True)
    wheel = tmp_path / "powercontext-2.0.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("powercontext-2.0.0.dist-info/METADATA", "Name: powercontext\nVersion: 2.0.0\n")
    return tmp_path, wheel, policy


def test_required_mode_executes_three_cases_but_cannot_publish_as_supported(release):
    root, wheel, policy = release
    with pytest.raises(gate.GateError, match="support declaration"):
        gate.gate(root, wheel, root / "evidence", release=True, release_notes=gate.support_text(policy, "en"))
    result = json.loads((root / "evidence/result.json").read_text())
    assert result["status"] == "failed"
    assert len(result["cases"]) == 3
    assert all(case["status"] == "passed" for case in result["cases"])
    assert result["target_wheel_sha256"] == gate.sha256(wheel)


def test_release_notes_cannot_omit_the_support_limit(release):
    root, wheel, _ = release
    with pytest.raises(gate.GateError, match="notes"):
        gate.gate(root, wheel, root / "evidence", release=True, release_notes="Upgrade your deployment now.")


def test_release_rejects_wrong_artifact_version(release):
    root, wheel, _ = release
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("powercontext-9.0.0.dist-info/METADATA", "Name: powercontext\nVersion: 9.0.0\n")
    with pytest.raises(gate.GateError, match="Wheel version"):
        gate.gate(root, wheel, root / "evidence", release=True)


def test_schema_change_needs_a_new_compatibility_review(release):
    root, _, _ = release
    (root / "src/powercontext/builtin/persistence/tables.py").write_text("# different schema\n")
    with pytest.raises(gate.GateError, match="Persistence changed"):
        gate.load_policy(root)


def test_supported_mode_cannot_succeed_with_an_empty_matrix(release):
    root, _, policy = release
    policy["mode"] = "supported-upgrades"
    policy["automatic_sources"] = None
    (root / gate.MANIFEST).write_text(json.dumps(policy))
    with pytest.raises(gate.GateError, match="nonempty matrix"):
        gate.load_policy(root)


@pytest.mark.parametrize(
    "relative",
    [
        "src/powercontext/server/identity.py",
        "src/powercontext/server/authz/repository.py",
        "src/powercontext/builtin/code/seekdb.py",
        "src/powercontext/limits.py",
    ],
)
def test_schema_owners_outside_persistence_need_compatibility_review(release, relative):
    root, _, _ = release
    owner = root / relative
    owner.parent.mkdir(parents=True, exist_ok=True)
    owner.write_text("# changed managed schema input\n")
    with pytest.raises(gate.GateError, match="Persistence changed"):
        gate.load_policy(root)


def supported_case(root, policy):
    policy["mode"] = "supported-upgrades"
    policy["automatic_sources"] = None
    policy["cases"] = [
        {
            "source_version": "1.3.0",
            "backend": backend,
            "configuration": "full-server",
            "suite": "tests/release_upgrades/test_example.py",
        }
        for backend in ("sqlite", "seekdb", "oceanbase")
    ]
    (root / gate.MANIFEST).write_text(json.dumps(policy))
    gate.check_docs(root, policy, write=True)


@pytest.mark.parametrize(
    "blocker", ["migration_required", "unmanaged full Server layout", "migration_framework_not_ready"]
)
def test_any_upgrade_stage_failure_blocks_release_and_records_failure(release, monkeypatch, blocker):
    root, wheel, policy = release
    supported_case(root, policy)

    def fail(*_args):
        raise gate.GateError(blocker)

    monkeypatch.setattr(gate, "run_case", fail)
    with pytest.raises(gate.GateError, match="Required upgrade acceptance failed"):
        gate.gate(root, wheel, root / "evidence")
    evidence = json.loads((root / "evidence/result.json").read_text())
    assert evidence["status"] == "failed"
    assert len(evidence["cases"]) == 3
    assert all(case["status"] == "failed" and blocker in case["error"] for case in evidence["cases"])


def test_missing_suite_cannot_be_advertised(release):
    root, _, policy = release
    supported_case(root, policy)
    (root / policy["cases"][0]["suite"]).unlink()
    with pytest.raises(gate.GateError, match="Missing acceptance suite"):
        gate.load_policy(root)


def test_installation_guidance_cannot_drift_from_matrix(release):
    root, _, policy = release
    page = root / "docs/en/docs/get-started/install-and-run.md"
    page.write_text(page.read_text().replace(gate.NOTICE["en"], "All upgrades are supported."))
    with pytest.raises(gate.GateError, match="instructions differ"):
        gate.check_docs(root, policy)


@pytest.mark.parametrize("invalid", ["empty", "missing-startup", "skipped", "failure", "error", "truncated"])
def test_partial_or_skipped_acceptance_is_not_passing_evidence(tmp_path, invalid):
    report = tmp_path / "junit.xml"
    suite = ElementTree.Element("testsuite")
    for phase in sorted(gate.PHASES):
        if invalid == "empty" or (invalid == "missing-startup" and phase == "test_target_startup"):
            continue
        case = ElementTree.SubElement(suite, "testcase", name=phase)
        if phase == "test_target_startup" and invalid in {"skipped", "failure", "error"}:
            ElementTree.SubElement(case, invalid)
    ElementTree.ElementTree(suite).write(report)
    if invalid == "truncated":
        report.write_text("<testsuite>")
    with pytest.raises((gate.GateError, ElementTree.ParseError)):
        gate.validate_junit(report)


def test_complete_passing_report_is_accepted(tmp_path):
    report = tmp_path / "junit.xml"
    suite = ElementTree.Element("testsuite")
    for phase in gate.PHASES:
        ElementTree.SubElement(suite, "testcase", name=phase)
    ElementTree.ElementTree(suite).write(report)
    gate.validate_junit(report)


def test_missing_backend_evidence_fails_even_with_a_passing_report(tmp_path):
    report = tmp_path / "backend.json"
    report.write_text(json.dumps({"backend": "oceanbase", "engine_version": "4.6.3"}))
    with pytest.raises(gate.GateError, match="driver_version"):
        gate.validate_backend_evidence(report, "oceanbase")


def test_old_evidence_directory_cannot_be_reused(release):
    root, wheel, _ = release
    gate.gate(root, wheel, root / "evidence")
    with pytest.raises(FileExistsError):
        gate.gate(root, wheel, root / "evidence")


def catalog_for(*versions):
    return {
        "releases": {
            version: [
                {
                    "filename": f"powercontext-{version}-py3-none-any.whl",
                    "packagetype": "bdist_wheel",
                    "yanked": False,
                    "url": f"https://files.pythonhosted.org/packages/powercontext-{version}-py3-none-any.whl",
                    "digests": {"sha256": "a" * 64},
                }
            ]
            for version in versions
        }
    }


def automatic_policy(policy):
    policy["automatic_sources"] = {
        "strategy": "fixed-version",
        "source_version": "1.3.0",
        "profiles": [
            {
                "backend": backend,
                "configuration": "full-server-no-vector",
                "suite": "tests/release_upgrades/test_example.py",
            }
            for backend in ("sqlite", "seekdb", "oceanbase")
        ],
    }


@pytest.mark.parametrize("target", ["1.4.0", "1.4.1", "1.5.0", "1.5.1.dev2"])
def test_fixed_source_tracks_each_target_on_all_three_backends(release, monkeypatch, target):
    _, _, policy = release
    automatic_policy(policy)
    monkeypatch.setattr(
        matrix, "fetch_catalog", lambda: catalog_for("1.3.0", "1.4.0", "1.4.1", "1.5.0rc1", "1.5.0", "1.6.0")
    )
    resolved = matrix.resolve_matrix(policy, target)
    assert [(case["source_version"], case["backend"]) for case in resolved["candidate_cases"]] == [
        ("1.3.0", backend) for backend in ("sqlite", "seekdb", "oceanbase")
    ]
    assert resolved["required_cases"] == resolved["candidate_cases"]
    assert all(case["source_artifact"]["sha256"] == "a" * 64 for case in resolved["candidate_cases"])


@pytest.mark.parametrize("target", ["1.4.1", "1.5.0"])
def test_reviewed_sources_each_require_every_backend_and_configuration(release, monkeypatch, target):
    _, _, policy = release
    profiles = [
        {
            "backend": backend,
            "configuration": configuration,
            "suite": "tests/release_upgrades/test_example.py",
        }
        for backend in ("sqlite", "seekdb", "oceanbase")
        for configuration in (
            "full-server-no-vector",
            "full-server-code",
            "full-server-vector",
            "full-server-vector-code",
        )
    ]
    policy["automatic_sources"]["profiles"] = profiles
    policy["cases"] = [{"source_version": "1.4.0", **profile} for profile in profiles]
    catalog = catalog_for("1.3.0", "1.4.0", "1.4.1", "1.5.0")
    catalog["releases"]["1.4.0"][0]["digests"]["sha256"] = "b" * 64
    monkeypatch.setattr(matrix, "fetch_catalog", lambda: catalog)
    resolved = matrix.resolve_matrix(policy, target)
    assert resolved["required_cases"] == resolved["candidate_cases"]
    assert len(resolved["required_cases"]) == 24
    assert {
        (case["source_version"], case["backend"], case["configuration"]) for case in resolved["required_cases"]
    } == {
        (source, profile["backend"], profile["configuration"]) for source in ("1.3.0", "1.4.0") for profile in profiles
    }
    for case in resolved["required_cases"]:
        assert case["source_artifact"]["sha256"] == {"1.3.0": "a" * 64, "1.4.0": "b" * 64}[case["source_version"]]
    del catalog["releases"]["1.4.0"]
    with pytest.raises(ValueError, match=r"1\.4\.0"):
        matrix.resolve_matrix(policy, target)


def test_newly_published_versions_do_not_change_the_fixed_source(release, monkeypatch):
    _, _, policy = release
    automatic_policy(policy)
    catalog = catalog_for("1.3.0")
    monkeypatch.setattr(matrix, "fetch_catalog", lambda: catalog)
    first = matrix.resolve_matrix(policy, "1.5.0")
    catalog["releases"].update(catalog_for("1.4.1")["releases"])
    second = matrix.resolve_matrix(policy, "1.5.0")
    assert first["candidate_cases"] == second["candidate_cases"]
    assert len(second["candidate_cases"]) == 3


@pytest.mark.parametrize("target", ["1.2.0", "1.3.0", "1.3.0rc1"])
def test_fixed_source_must_precede_target(release, target):
    _, _, policy = release
    automatic_policy(policy)
    with pytest.raises(ValueError, match="must precede target"):
        matrix.resolve_matrix(policy, target)


@pytest.mark.parametrize("broken", ["empty", "missing-source", "yanked", "missing-wheel", "bad-hash", "foreign-origin"])
def test_unavailable_source_cannot_silently_shrink_matrix(release, monkeypatch, broken):
    _, _, policy = release
    automatic_policy(policy)
    catalog = catalog_for("1.3.0", "1.4.1")
    wheel = catalog["releases"]["1.3.0"][0]
    if broken == "empty":
        catalog["releases"] = {}
    elif broken == "missing-source":
        del catalog["releases"]["1.3.0"]
    elif broken == "yanked":
        wheel["yanked"] = True
    elif broken == "missing-wheel":
        catalog["releases"]["1.3.0"] = []
    elif broken == "bad-hash":
        wheel["digests"]["sha256"] = "invalid"
    else:
        wheel["url"] = "https://untrusted.invalid/package.whl"
    monkeypatch.setattr(matrix, "fetch_catalog", lambda: catalog)
    with pytest.raises(ValueError):
        matrix.resolve_matrix(policy, "1.5.0")


def test_catalog_outage_records_a_failed_gate(release, monkeypatch):
    root, wheel, policy = release
    automatic_policy(policy)
    (root / gate.MANIFEST).write_text(json.dumps(policy))

    def unavailable():
        raise OSError("Catalog unavailable")  # noqa: TRY003 -- exercise a distinct discovery outage

    monkeypatch.setattr(matrix, "fetch_catalog", unavailable)
    with pytest.raises(OSError, match="Catalog unavailable"):
        gate.gate(root, wheel, root / "evidence")
    assert json.loads((root / "evidence/result.json").read_text())["status"] == "failed"


def test_switching_to_supported_upgrades_requires_all_backend_suites(release):
    root, _, policy = release
    automatic_policy(policy)
    policy["automatic_sources"]["profiles"][0]["suite"] = None
    (root / gate.MANIFEST).write_text(json.dumps(policy))
    with pytest.raises(gate.GateError, match="every backend profile"):
        gate.load_policy(root)


def test_supported_fixed_source_executes_every_backend(release, monkeypatch):
    root, wheel, policy = release
    supported_case(root, policy)
    suite = policy["cases"][0]["suite"]
    policy["cases"] = []
    automatic_policy(policy)
    for profile in policy["automatic_sources"]["profiles"]:
        profile["suite"] = suite
    (root / gate.MANIFEST).write_text(json.dumps(policy))
    gate.check_docs(root, policy, write=True)
    monkeypatch.setattr(matrix, "fetch_catalog", lambda: catalog_for("1.3.0", "1.4.1"))
    executed = []

    def execute(_root, _wheel, case, _output):
        executed.append((case["source_version"], case["backend"]))
        return {"case": case, "status": "passed"}

    monkeypatch.setattr(gate, "run_case", execute)
    gate.gate(root, wheel, root / "evidence")
    assert executed == [(version, backend) for version in ("1.3.0",) for backend in ("sqlite", "seekdb", "oceanbase")]
    result = json.loads((root / "evidence/result.json").read_text())
    assert len(result["cases"]) == len(result["resolved_matrix"]["required_cases"]) == 3


def test_publishing_jobs_require_upgrade_acceptance_unless_manually_bypassed():
    root = Path(__file__).parents[1]
    workflow = yaml.safe_load((root / ".github/workflows/release.yml").read_text())
    build = workflow["jobs"]["release-build"]
    assert build["uses"] == "./.github/workflows/upgrade-release.yml"
    assert build["with"]["release"] is True
    assert build["with"]["selection"] == "full"
    assert build["with"]["skip_upgrade_acceptance"] == (
        "${{ github.event_name == 'workflow_dispatch' && inputs.skip_upgrade_acceptance }}"
    )
    events = workflow.get("on", workflow.get(True))  # PyYAML's YAML 1.1 parser treats "on" as True.
    assert events["workflow_dispatch"]["inputs"]["skip_upgrade_acceptance"]["default"] is False
    assert not build.get("continue-on-error")
    shared = yaml.safe_load((root / ".github/workflows/upgrade-release.yml").read_text())["jobs"]
    prepare = shared["prepare"]["steps"]
    freeze = next(step for step in prepare if "parallel_upgrade_release_gate.py prepare" in step.get("run", ""))
    assert "--release --release-event" in freeze["run"]
    steps = shared["collect"]["steps"]
    index = next(i for i, step in enumerate(steps) if "parallel_upgrade_release_gate.py collect" in step.get("run", ""))
    assert "if" not in steps[index] and not steps[index].get("continue-on-error")
    upload = next(i for i, step in enumerate(steps) if step.get("with", {}).get("name") == "release-dists")
    assert index < upload
    assert steps[upload]["if"] == "success() && inputs.release"
    assert steps[upload]["with"]["path"] == "${{ runner.temp }}/upgrade-release-evidence/validated-dist/*"
    assert shared["collect"]["needs"] == ["prepare", "cases"]
    assert any('test "$CASE_JOBS_RESULT" = success' in step.get("run", "") for step in steps)
    for name in ("pypi-publish", "release-assets"):
        dependencies = workflow["jobs"][name]["needs"]
        assert "release-build" in ([dependencies] if isinstance(dependencies, str) else dependencies)
    main = yaml.safe_load((root / ".github/workflows/master.yml").read_text())["jobs"]
    assert main["upgrade-acceptance"]["uses"] == build["uses"]
    verdict = main["upgrade-release-gate"]
    assert verdict["name"] == "Upgrade release gate" and verdict["if"] == "always()"
    assert verdict["needs"] == "upgrade-acceptance"
    assert verdict["steps"][0]["run"] == 'test "$UPGRADE_RESULT" = success'


@pytest.mark.parametrize(
    "configuration", ["full-server-no-vector", "full-server-code", "full-server-vector", "full-server-vector-code"]
)
def test_repository_baseline_covers_all_three_databases(configuration, monkeypatch):
    policy = gate.load_policy(Path(__file__).parents[1])
    selection = policy["automatic_sources"]
    assert selection["strategy"] == "fixed-version"
    assert selection["source_version"] == "1.3.0"
    assert {profile["backend"] for profile in selection["profiles"]} == {"sqlite", "seekdb", "oceanbase"}
    assert {profile["backend"] for profile in selection["profiles"] if profile["configuration"] == configuration} == {
        "sqlite",
        "seekdb",
        "oceanbase",
    }
    monkeypatch.setattr(matrix, "fetch_catalog", lambda: catalog_for("1.3.0", "1.4.0", "1.4.1"))
    resolved = matrix.resolve_matrix(policy, policy["target_version"])
    assert len(resolved["required_cases"]) == 12
    assert {case["source_version"] for case in resolved["required_cases"]} == {"1.3.0"}
    assert {case["backend"] for case in resolved["required_cases"] if case["configuration"] == configuration} == {
        "sqlite",
        "seekdb",
        "oceanbase",
    }


def test_upgrade_cases_require_isolated_backends_and_always_clean_up():
    root = Path(__file__).parents[1]
    workflow = yaml.safe_load((root / ".github/workflows/upgrade-release.yml").read_text())
    job = workflow["jobs"]["cases"]
    assert job["needs"] == "prepare"
    assert job["strategy"]["fail-fast"] is False
    assert job["strategy"]["matrix"] == "${{ fromJSON(needs.prepare.outputs.matrix) }}"
    steps = job["steps"]
    setup_index = next(
        i for i, step in enumerate(steps) if step.get("uses") == "./.github/actions/setup-upgrade-oceanbase"
    )
    gate_index = next(
        i for i, step in enumerate(steps) if "parallel_upgrade_release_gate.py case" in step.get("run", "")
    )
    assert setup_index < gate_index
    assert steps[setup_index]["if"] == "matrix.backend == 'oceanbase'"
    assert "if" not in steps[gate_index]
    assert not any(step.get("continue-on-error") for step in steps)
    assert not any("ci_legacy_upgrade_rejection.py" in step.get("run", "") for step in steps)
    assert "/pc_upgrade_test?" in job["env"]["PC_UPGRADE_OCEANBASE_URL"]
    assert "/pc_upgrade_fresh?" in job["env"]["PC_UPGRADE_OCEANBASE_FRESH_URL"]
    cleanup = next(step for step in steps if "docker rm --force upgrade-oceanbase" in step.get("run", ""))
    assert cleanup["if"] == "always() && matrix.backend == 'oceanbase'"


def test_oceanbase_sources_and_profiles_use_separate_provisioned_databases(tmp_path, monkeypatch):
    urls = {
        "PC_UPGRADE_OCEANBASE_URL": "mysql+aoceanbase://root%40test:fixture@127.0.0.1:2881/pc_upgrade_test?charset=utf8mb4",
        "PC_UPGRADE_OCEANBASE_FRESH_URL": "mysql+aoceanbase://root%40test:fixture@127.0.0.1:2881/pc_upgrade_fresh?charset=utf8mb4",
    }
    for key, value in urls.items():
        monkeypatch.setenv(key, value)
    action = yaml.safe_load(
        (Path(__file__).parents[1] / ".github/actions/setup-upgrade-oceanbase/action.yml").read_text()
    )
    setup = action["runs"]["steps"][0]["run"]
    provisioned = set(re.findall(r"CREATE DATABASE (pc_upgrade[a-z0-9_]*)", setup))
    provisioned.update(re.findall(r"--env OB_DATABASE=(pc_upgrade[a-z0-9_]*)", setup))
    selected = set()
    for source in ("1.3.0", "1.4.0"):
        for configuration in (
            "full-server-no-vector",
            "full-server-code",
            "full-server-vector",
            "full-server-vector-code",
        ):
            case = {
                "source_version": source,
                "backend": "oceanbase",
                "configuration": configuration,
                "code_enabled": configuration.endswith("-code"),
                "vector_dimension": 0,
                "model": {"url": "http://127.0.0.1:1/v1"},
                "work": tmp_path,
            }
            for fresh in (False, True):
                environment = environment_for(case, fresh=fresh)
                selected_url = urlsplit(environment["POWERCONTEXT_SERVER_DATABASE_URL"])
                assert selected_url.geturl() == environment["PC_UPGRADE_OCEANBASE_URL"]
                assert selected_url.path not in selected, "Source/configuration cases must not reuse stored data"
                assert selected_url.path.removeprefix("/") in provisioned
                selected.add(selected_url.path)
                original = urlsplit(urls["PC_UPGRADE_OCEANBASE_FRESH_URL" if fresh else "PC_UPGRADE_OCEANBASE_URL"])
                assert selected_url._replace(path=original.path) == original
    assert len(selected) == 16
    assert {key: os.environ[key] for key in urls} == urls


@pytest.mark.parametrize(
    "database", ["production", "pc_upgrade_vector_test_deadbeef", "pc_upgrade_140_code_test_deadbeef"]
)
def test_oceanbase_source_adapter_rejects_other_databases(tmp_path, monkeypatch, database):
    monkeypatch.setenv(
        "PC_UPGRADE_OCEANBASE_URL",
        f"mysql+aoceanbase://root%40test:fixture@127.0.0.1:2881/{database}?charset=utf8mb4",
    )
    case = {
        "source_version": "1.4.0",
        "backend": "oceanbase",
        "configuration": "full-server-vector",
        "code_enabled": False,
        "vector_dimension": 3,
        "model": {"url": "http://127.0.0.1:1/v1"},
        "work": tmp_path,
    }
    with pytest.raises(AssertionError, match="dedicated disposable"):
        environment_for(case)


def test_oceanbase_fixture_selects_the_oceanbase_server_profile(tmp_path, monkeypatch):
    for key in os.environ:
        if key.startswith("POWERCONTEXT_"):
            monkeypatch.delenv(key)
    url = "mysql+aoceanbase://root%40test:test@127.0.0.1:2881/pc_upgrade_test?charset=utf8mb4"
    for key, value in database_environment("oceanbase", tmp_path / "unused", {"PC_UPGRADE_OCEANBASE_URL": url}).items():
        monkeypatch.setenv(key, value)
    settings = ServerSettings()
    assert settings.database.kind == "oceanbase"
    assert settings.database.url.get_secret_value() == url


@pytest.mark.parametrize("mode", ["fresh-install-only", "upgrade-required", "supported-upgrades"])
def test_declarations_cannot_bypass_missing_backend_or_suite(release, mode):
    root, _, policy = release
    policy["mode"] = mode
    policy["automatic_sources"]["profiles"] = policy["automatic_sources"]["profiles"][:2]
    (root / gate.MANIFEST).write_text(json.dumps(policy))
    with pytest.raises(gate.GateError):
        gate.load_policy(root)


def test_fresh_install_only_is_not_an_upgrade_escape_hatch(release):
    root, _, policy = release
    policy["mode"] = "fresh-install-only"
    (root / gate.MANIFEST).write_text(json.dumps(policy))
    with pytest.raises(gate.GateError, match="mode"):
        gate.load_policy(root)


def test_failed_backend_does_not_skip_other_required_backends(release, monkeypatch):
    root, wheel, _ = release
    executed = []

    def execute(_root, _wheel, case, _output):
        executed.append(case["backend"])
        if case["backend"] == "sqlite":
            raise gate.GateError("migration_required")
        return {"case": case, "status": "passed"}

    monkeypatch.setattr(gate, "run_case", execute)
    with pytest.raises(gate.GateError, match="failed: sqlite"):
        gate.gate(root, wheel, root / "evidence")
    assert executed == ["sqlite", "seekdb", "oceanbase"]
    result = json.loads((root / "evidence/result.json").read_text())
    assert result["status"] == "failed"
    assert [case["status"] for case in result["cases"]] == ["failed", "passed", "passed"]


def test_failed_seekdb_acceptance_keeps_native_logs_before_workspace_cleanup(tmp_path):
    output = tmp_path / "evidence"
    output.mkdir()
    journal = b'{"state":"active","last_revision":"s1300","pending_revision":"s1400"}'
    maintenance = "database.pc-migration-state/disposable-database/maintenance.json"
    fresh_maintenance = "fresh.pc-migration-state/disposable-fresh/maintenance.json"

    with pytest.raises(gate.GateError, match="fixture migration failed"), gate.case_workspace("seekdb", output) as work:
        native = work / "database/log"
        native.mkdir(parents=True)
        (native / "observer.log").write_bytes(b"native recovery diagnostic\n")
        (work / "database/store").write_bytes(b"database payload must not become a log")
        fresh = work / "fresh/log"
        fresh.mkdir(parents=True)
        (fresh / "observer.log.wf").write_bytes(b"fresh native diagnostic\n")
        for relative in (maintenance, fresh_maintenance):
            path = work / relative
            path.parent.mkdir(parents=True)
            path.write_bytes(journal)
            (path.parent / "backup.data").write_bytes(b"backup payload must not be collected")
        raise gate.GateError("fixture migration failed")  # noqa: TRY003 -- synthetic acceptance failure

    assert not work.exists()
    with zipfile.ZipFile(output / "seekdb-native-logs.zip") as archive:
        assert set(archive.namelist()) == {
            "database/log/observer.log",
            "fresh/log/observer.log.wf",
            maintenance,
            fresh_maintenance,
        }
        assert archive.read("database/log/observer.log") == b"native recovery diagnostic\n"
        assert archive.read("fresh/log/observer.log.wf") == b"fresh native diagnostic\n"
        assert archive.read(maintenance) == journal
        assert archive.read(fresh_maintenance) == journal
    manifest = json.loads((output / "seekdb-native-logs.json").read_text())
    assert len(manifest["files"]) == 4
    assert not any(item["truncated"] for item in manifest["files"])


def test_seekdb_diagnostics_failure_preserves_acceptance_failure(tmp_path, capsys):
    with (
        pytest.raises(gate.GateError, match="fixture migration failed"),
        gate.case_workspace("seekdb", tmp_path / "missing-output") as work,
    ):
        raise gate.GateError("fixture migration failed")  # noqa: TRY003 -- synthetic acceptance failure

    assert not work.exists()
    assert "seekDB native log collection failed: FileNotFoundError" in capsys.readouterr().err


def test_seekdb_diagnostics_keep_bounded_head_and_tail_of_large_native_logs(tmp_path):
    output = tmp_path / "evidence"
    output.mkdir()
    content = b"initial DDL diagnostic\n" + b"x" * (5 * 1024 * 1024) + b"final wait diagnostic\n"
    with gate.case_workspace("seekdb", output) as work:
        native = work / "database/log"
        native.mkdir(parents=True)
        (native / "observer.log").write_bytes(content)

    with zipfile.ZipFile(output / "seekdb-native-logs.zip") as archive:
        head = archive.read("head/database/log/observer.log")
        tail = archive.read("tail/database/log/observer.log")
        assert len(head) + len(tail) <= 4 * 1024 * 1024
        assert head.startswith(b"initial DDL diagnostic\n")
        assert tail.endswith(b"final wait diagnostic\n")
        manifest = json.loads((output / "seekdb-native-logs.json").read_text())
        record = manifest["files"][0]
        assert record["truncated"] is True
        assert record["size_bytes"] == len(content)
        assert record["captured_bytes"] == len(head) + len(tail)
        for segment in record["segments"]:
            start = segment["offset_bytes"]
            assert archive.read(segment["path"]) == content[start : start + segment["captured_bytes"]]


def test_seekdb_diagnostics_share_one_budget_with_maintenance_evidence(tmp_path):
    output = tmp_path / "evidence"
    output.mkdir()
    maintenance = "database.pc-migration-state/disposable-database/maintenance.json"
    journal = b'{"state":"active","pending_revision":"s1400"}'
    with gate.case_workspace("seekdb", output) as work:
        path = work / maintenance
        path.parent.mkdir(parents=True)
        path.write_bytes(journal)
        for store in ("database", "fresh"):
            native = work / store / "log"
            native.mkdir(parents=True)
            for number in range(3):
                (native / f"observer.log.{number}").write_bytes(b"x" * (5 * 1024 * 1024))

    manifest = json.loads((output / "seekdb-native-logs.json").read_text())
    with zipfile.ZipFile(output / "seekdb-native-logs.zip") as archive:
        assert archive.read(maintenance) == journal
        captured = sum(entry.file_size for entry in archive.infolist())
        assert captured <= 16 * 1024 * 1024
        assert captured == sum(record["captured_bytes"] for record in manifest["files"])
    assert manifest["budget_exhausted"] is True


@pytest.mark.parametrize(
    "relative",
    [
        "src/powercontext/builtin/persistence/tables.py",
        "src/powercontext/builtin/persistence/revision.sql",
        "src/powercontext/builtin/code/layout.json",
        "src/powercontext/server/authz/repository.py",
        "src/powercontext/server/identity.py",
        "src/powercontext/limits.py",
    ],
)
def test_checkout_line_endings_preserve_compatibility_review(release, relative):
    root, _, policy = release
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"first line\nsecond line\n")
    policy["schema_review"]["persistence_sha256"] = gate.managed_digest(root)
    (root / gate.MANIFEST).write_text(json.dumps(policy))
    path.write_bytes(b"first line\r\nsecond line\r\n")
    assert gate.load_policy(root)["schema_review"] == policy["schema_review"]
    path.write_bytes(b"first line\r\nchanged content\r\n")
    with pytest.raises(gate.GateError, match="file set or content; CRLF/LF normalized"):
        gate.load_policy(root)


def test_artifact_hashes_do_not_normalize_bytes(tmp_path):
    artifact = tmp_path / "artifact"
    artifact.write_bytes(b"payload\n")
    before = gate.sha256(artifact)
    artifact.write_bytes(b"payload\r\n")
    assert gate.sha256(artifact) != before


_PROCESS_FIXTURE = """
import os, signal, subprocess, sys, time
from pathlib import Path
from urllib.parse import urlsplit

role, mode, control, evidence = sys.argv[1:]
control, evidence = Path(control), Path(evidence)
child = None
if mode == "forced-timeout":
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
else:
    def stop(_signal, _frame):
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, stop)
try:
    ready = evidence / (role + ".ready")
    ready.write_text(str(os.getpid()))
    ready.replace(evidence / (role + ".pid"))
    if role != "engine":
        next_role = "server" if role == "root" else "engine"
        child = subprocess.Popen(
            [sys.executable, __file__, next_role, mode, str(control), str(evidence)],
            start_new_session=True,
        )
    if role == "root" and mode in {"success", "failure"}:
        while not (evidence / "engine.pid").exists():
            time.sleep(0.02)
        # Deliberately leave both detached generations for the command owner.
        os._exit(0 if mode == "success" else 7)
    while control.exists():
        time.sleep(0.02)
finally:
    if child is not None:
        child.terminate()
        try:
            child.wait(timeout=2)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()
    (evidence / (role + ".closed")).write_text("closed")
"""


@pytest.mark.skipif(sys.platform != "linux", reason="The upgrade supervisor uses Linux subreaper ownership")
@pytest.mark.parametrize("mode", ["success", "failure", "timeout", "forced-timeout"])
def test_upgrade_command_reaps_detached_descendants_before_workspace_cleanup(tmp_path, mode):
    fixture = tmp_path / "process_fixture.py"
    fixture.write_text(_PROCESS_FIXTURE)
    control = tmp_path / "keep-running"
    control.touch()
    report = tmp_path / "evidence"
    report.mkdir()
    # This child belongs to the test, not the isolated command supervisor.
    sentinel = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        with gate.case_workspace("sqlite", report) as work:
            arguments = [sys.executable, str(fixture), "root", mode, str(control), str(report)]
            options = {
                "environment": dict(os.environ),
                "cwd": work,
                "log": report / "command.log",
                "timeout": 5 if mode.endswith("timeout") else 20,
                "shutdown_timeout": 0.5,
            }
            if mode == "success":
                gate.run_owned(arguments, **options)
            else:
                with pytest.raises(gate.GateError, match="Command failed"):
                    gate.run_owned(arguments, **options)
            assert sentinel.poll() is None
            for role in ("root", "server", "engine"):
                pid = int((report / f"{role}.pid").read_text())
                with pytest.raises(ProcessLookupError):
                    os.kill(pid, 0)
            if mode != "forced-timeout":
                assert (report / "server.closed").is_file()
                assert (report / "engine.closed").is_file()
            if mode.endswith("timeout"):
                assert "exceeded 5 seconds" in (report / "command.log").read_text()
        assert not work.exists()
    finally:
        # Also let controlled fixture processes exit if the regression fails.
        # Never signal a stale PID obtained from a fixture file.
        control.unlink(missing_ok=True)
        sentinel.terminate()
        sentinel.wait(timeout=5)


@pytest.mark.skipif(sys.platform != "linux", reason="The upgrade supervisor uses Linux subreaper ownership")
@pytest.mark.parametrize("cancellation", ["parent-term", "repeat-interrupt"])
def test_upgrade_command_owner_finishes_cleanup_after_gate_cancellation(tmp_path, cancellation):
    fixture = tmp_path / "process_fixture.py"
    fixture.write_text(_PROCESS_FIXTURE)
    control = tmp_path / "keep-running"
    control.touch()
    report = tmp_path / "evidence"
    report.mkdir()
    owner_script = tmp_path / "owner.py"
    owner_script.write_text(
        "import os,sys\nfrom pathlib import Path\n"
        f"sys.path[:0] = {[str(Path(gate.__file__).parent), str(Path(matrix.__file__).parent)]!r}\n"
        "import upgrade_release_gate as gate\n"
        f"report = Path({str(report)!r})\n"
        "with gate.case_workspace('sqlite', report) as work:\n"
        "    (report / 'workspace').write_text(str(work))\n"
        f"    gate.run_owned({[sys.executable, str(fixture), 'root', 'forced-timeout', str(control), str(report)]!r}, "
        "environment=dict(os.environ), cwd=work, log=report / 'command.log', timeout=60, shutdown_timeout=0.5)\n"
    )
    sentinel = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    with (report / "owner.log").open("w") as log:
        owner = subprocess.Popen([sys.executable, str(owner_script)], stdout=log, stderr=subprocess.STDOUT)
        pids = []
        try:
            deadline = time.monotonic() + 15
            while not (report / "engine.pid").exists():
                assert owner.poll() is None, (report / "owner.log").read_text()
                assert time.monotonic() < deadline, "Controlled processes did not become ready"
                time.sleep(0.02)
            pids = [int((report / f"{role}.pid").read_text()) for role in ("root", "server", "engine")]
            work = Path((report / "workspace").read_text())
            owner.send_signal(signal.SIGTERM if cancellation == "parent-term" else signal.SIGINT)
            if cancellation == "repeat-interrupt":
                time.sleep(0.1)
                assert owner.poll() is None, "Cancellation did not wait for owned processes"
                owner.send_signal(signal.SIGINT)
            assert owner.wait(timeout=10) != 0
            if cancellation == "parent-term":
                # The dead gate cannot await its owner; PDEATHSIG initiates cleanup.
                deadline = time.monotonic() + 10
                while any(_process_exists(pid) for pid in pids):
                    assert time.monotonic() < deadline, "Detached descendants survived gate termination"
                    time.sleep(0.02)
            else:
                # The second interrupt must not delete the workspace early.
                assert not any(_process_exists(pid) for pid in pids)
                assert not work.exists()
            assert sentinel.poll() is None
        finally:
            control.unlink(missing_ok=True)
            if owner.poll() is None:
                owner.terminate()
                owner.wait(timeout=5)
            sentinel.terminate()
            sentinel.wait(timeout=5)
            if cancellation == "parent-term" and pids and not any(_process_exists(pid) for pid in pids):
                shutil.rmtree(Path((report / "workspace").read_text()), ignore_errors=True)


def _process_exists(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


@pytest.fixture
def parallel_reports(release, monkeypatch, request):
    root, wheel, policy = release
    selection = getattr(request, "param", "full")
    if hasattr(request, "param"):
        policy["automatic_sources"]["profiles"] = [
            {"backend": backend, "configuration": configuration, "suite": "tests/release_upgrades/test_example.py"}
            for backend in ("sqlite", "seekdb", "oceanbase")
            for configuration in (
                "full-server-no-vector",
                "full-server-vector",
                "full-server-code",
                "full-server-vector-code",
            )
        ]
        policy["mode"] = "supported-upgrades"
        parallel.write_json(root / gate.MANIFEST, policy)
        gate.check_docs(root, policy, write=True)
    dist = root / "dist"
    dist.mkdir()
    shutil.copyfile(wheel, dist / wheel.name)
    # Model an unpinned checkout even inside the upgrade workflow, which exports PC_UPGRADE_SOURCE_SHA.
    monkeypatch.delenv("PC_UPGRADE_SOURCE_SHA", raising=False)
    prepared = root / "prepared"
    scheduled = parallel.prepare(root, dist, prepared, release=False, release_notes=None, selection=selection)
    executed = []

    def execute(_root, tested_wheel, case, output):
        executed.append(case)
        assert gate.sha256(tested_wheel) == gate.sha256(wheel)
        output.mkdir()
        installed = {"source": {"powercontext": case["source_version"]}, "target": {"powercontext": "2.0.0"}}
        backend = {
            "backend": case["backend"],
            "configuration": case["configuration"],
            "engine_version": "fixture",
            "driver_version": "fixture",
            "source_schema": "source",
            "target_schema": "target",
            "fresh_schema": "fresh",
            "tenant_mode": "mysql",
            "proxy_version": "direct",
        }
        if case["configuration"] in {"full-server-code", "full-server-vector-code"}:
            backend["code"] = {
                "fingerprint": "a" * 64,
                "immutable_cache_files_preserved": True,
                "symbol_ids_preserved": True,
                "server_query_ready": True,
            }
        if case["configuration"] in {"full-server-vector", "full-server-vector-code"}:
            backend["vectors"] = {
                "dimension": 4 if case["configuration"].endswith("-code") else 3,
                "maintenance_embedding_requests": 0,
                "historical_atomic_and_topic_embeddings_preserved": True,
                "historical_and_new_vector_reads": True,
                "fresh_vector_read": True,
                "profile_drift_rejected": True,
                "missing_sqlite_helper_rejected": True,
            }
        parallel.write_json(output / "installed.json", installed)
        parallel.write_json(output / "backend.json", backend)
        tree = ElementTree.Element("testsuite")
        for phase in gate.PHASES:
            ElementTree.SubElement(tree, "testcase", name=phase)
        ElementTree.ElementTree(tree).write(output / "junit.xml")
        return {"case": case, "status": "passed", "installed": installed, "database": backend}

    monkeypatch.setattr(gate, "run_case", execute)
    # Source discovery must not recur on independent workers or during collection.
    monkeypatch.setattr(matrix, "fetch_catalog", lambda: pytest.fail("Catalog resolved twice"))
    reports = root / "reports"
    for entry in reversed(scheduled["include"]):
        parallel.execute_case(root, prepared, entry["index"], reports / f"case-{entry['index']}")
    assert len(executed) == len(scheduled["include"])
    assert len({(case["source_version"], case["backend"], case["configuration"]) for case in executed}) == len(executed)
    return root, prepared, reports, policy


def test_parallel_collection_accepts_each_case_once_and_keeps_exact_artifacts(parallel_reports):
    root, prepared, reports, _ = parallel_reports
    accepted = parallel.collect(root, prepared, reports, root / "accepted")
    plan = json.loads((prepared / "plan.json").read_text())
    assert {path.name: gate.sha256(path) for path in accepted} == plan["artifacts"]
    verdict = json.loads((root / "accepted/result.json").read_text())
    assert verdict["status"] == "passed"
    assert [result["case"] for result in verdict["cases"]] == plan["resolved_matrix"]["required_cases"]


@pytest.mark.parametrize(
    "broken",
    [
        "missing",
        "duplicate",
        "stale",
        "failed",
        "wrong-case",
        "skipped",
        "missing-phase",
        "installed",
        "backend",
        "artifact",
    ],
)
def test_parallel_collection_rejects_incomplete_or_mixed_evidence(parallel_reports, broken):
    root, prepared, reports, _ = parallel_reports
    path = reports / "case-0/result.json"
    result = json.loads(path.read_text())
    if broken == "missing":
        path.unlink()
    elif broken == "duplicate":
        another = reports / "case-1/result.json"
        parallel.write_json(another, result)
    elif broken == "stale":
        result["plan_sha256"] = "0" * 64
        parallel.write_json(path, result)
    elif broken == "failed":
        result["status"] = "failed"
        parallel.write_json(path, result)
    elif broken == "wrong-case":
        result["result"]["case"]["configuration"] = "different-profile"
        parallel.write_json(path, result)
    elif broken in {"skipped", "missing-phase"}:
        report = path.parent / "acceptance/junit.xml"
        tree = ElementTree.parse(report)  # noqa: S314 -- disposable locally generated test report
        if broken == "skipped":
            ElementTree.SubElement(next(tree.iter("testcase")), "skipped")
        else:
            tree.getroot().remove(next(tree.iter("testcase")))
        tree.write(report)
    elif broken == "installed":
        parallel.write_json(path.parent / "acceptance/installed.json", {"target": {"powercontext": "old"}})
    elif broken == "backend":
        parallel.write_json(path.parent / "acceptance/backend.json", {"backend": "sqlite"})
    else:
        wheel = next((prepared / "inputs").glob("*.whl"))
        wheel.write_bytes(b"a different build")
    with pytest.raises(gate.GateError):
        parallel.collect(root, prepared, reports, root / "accepted")
    assert not (root / "accepted/validated-dist").exists()


def test_parallel_workers_reject_another_run(parallel_reports, monkeypatch):
    root, prepared, reports, _ = parallel_reports
    monkeypatch.setenv("GITHUB_RUN_ID", "another-run")
    with pytest.raises(gate.GateError, match="another workflow"):
        parallel.collect(root, prepared, reports, root / "accepted")


def test_manual_release_evidence_tracks_target_checkout_not_workflow_commit(release, monkeypatch):
    root, wheel, _ = release
    dist = root / "dist"
    dist.mkdir()
    shutil.copyfile(wheel, dist / wheel.name)
    monkeypatch.setenv("GITHUB_SHA", "workflow-commit")
    monkeypatch.setenv("PC_UPGRADE_SOURCE_SHA", "release-commit")
    prepared = root / "prepared"
    parallel.prepare(root, dist, prepared, release=False, release_notes=None)
    plan, _ = parallel.read_plan(root, prepared)
    assert plan["source_sha"] == "release-commit"
    monkeypatch.setenv("PC_UPGRADE_SOURCE_SHA", "another-release-commit")
    with pytest.raises(gate.GateError, match="another checkout"):
        parallel.read_plan(root, prepared)


def test_parallel_failed_case_still_records_failure_and_allows_other_workers(release, monkeypatch):
    root, wheel, _ = release
    dist = root / "dist"
    dist.mkdir()
    shutil.copyfile(wheel, dist / wheel.name)
    prepared = root / "prepared"
    parallel.prepare(root, dist, prepared, release=False, release_notes=None)

    def fail(*_args):
        raise gate.GateError("migration_required")

    monkeypatch.setattr(gate, "run_case", fail)
    with pytest.raises(gate.GateError, match="migration_required"):
        parallel.execute_case(root, prepared, 0, root / "reports/case-0")
    assert json.loads((root / "reports/case-0/result.json").read_text())["status"] == "failed"
    monkeypatch.setattr(gate, "run_case", lambda *_args: {"status": "passed"})
    parallel.execute_case(root, prepared, 1, root / "reports/case-1")
    assert json.loads((root / "reports/case-1/result.json").read_text())["status"] == "passed"


@pytest.mark.parametrize("changed", ["policy", "checkout", "matrix"])
def test_parallel_workers_reject_changed_preparation_inputs(parallel_reports, monkeypatch, changed):
    root, prepared, _reports, policy = parallel_reports
    if changed == "policy":
        policy["schema_review"]["rationale"] += " Changed review."
        parallel.write_json(root / gate.MANIFEST, policy)
    elif changed == "checkout":
        monkeypatch.setenv("GITHUB_SHA", "another-commit")
        monkeypatch.setenv("PC_UPGRADE_SOURCE_SHA", "another-commit")
    else:
        plan = json.loads((prepared / "plan.json").read_text())
        plan["resolved_matrix"]["required_cases"].pop()
        parallel.write_json(prepared / "plan.json", plan)
    with pytest.raises(gate.GateError):
        parallel.execute_case(root, prepared, 0, root / "another-case")
    assert not (root / "another-case").exists()


def test_parallel_passes_do_not_override_unreviewed_release_support(parallel_reports):
    root, prepared, reports, policy = parallel_reports
    # Model the release preparation metadata; distribution validation itself is
    # exercised with real sdist rebuilds in test_upgrade_publication.py.
    plan = json.loads((prepared / "plan.json").read_text())
    plan["release"] = True
    plan["release_notes"] = gate.support_text(policy, "en")
    parallel.write_json(prepared / "plan.json", plan)
    for path in reports.glob("*/result.json"):
        result = json.loads(path.read_text())
        result["plan_sha256"] = gate.sha256(prepared / "plan.json")
        parallel.write_json(path, result)
    with pytest.raises(gate.GateError, match="support declaration"):
        parallel.collect(root, prepared, reports, root / "accepted")
    assert not (root / "accepted/validated-dist").exists()


def test_parallel_worker_rejects_artifact_changed_during_execution(release, monkeypatch):
    root, wheel, _ = release
    dist = root / "dist"
    dist.mkdir()
    shutil.copyfile(wheel, dist / wheel.name)
    prepared = root / "prepared"
    parallel.prepare(root, dist, prepared, release=False, release_notes=None)

    def replace_wheel(_root, tested_wheel, _case, _output):
        tested_wheel.write_bytes(b"replaced while acceptance ran")
        return {"status": "passed"}

    monkeypatch.setattr(gate, "run_case", replace_wheel)
    with pytest.raises(gate.GateError, match="distributions changed"):
        parallel.execute_case(root, prepared, 0, root / "reports/case-0")
    assert json.loads((root / "reports/case-0/result.json").read_text())["status"] == "failed"


@pytest.mark.parametrize("parallel_reports", ["automatic", "seekdb-code", "full"], indirect=True)
def test_upgrade_selection_requires_all_cases_for_publication(parallel_reports):
    root, prepared, reports, policy = parallel_reports
    plan = json.loads((prepared / "plan.json").read_text())
    selection = plan["selection"]
    complete = selection != "seekdb-code"
    expected = 12 if complete else 2
    if complete:
        # Model release metadata; real sdist validation is covered separately.
        plan["release"] = True
        plan["release_notes"] = gate.support_text(policy, "en")
        parallel.write_json(prepared / "plan.json", plan)
        for path in reports.glob("*/result.json"):
            result = json.loads(path.read_text())
            result["plan_sha256"] = gate.sha256(prepared / "plan.json")
            parallel.write_json(path, result)
    accepted = parallel.collect(root, prepared, reports, root / "accepted")
    verdict = json.loads((root / "accepted/result.json").read_text())
    assert len(verdict["cases"]) == expected
    assert len(verdict["deferred_cases"]) == 12 - expected
    assert verdict["complete"] is complete
    assert bool(accepted) is complete
    if complete:
        assert verdict["status"] == "passed"
        publication = json.loads((root / "accepted/publication.json").read_text())
        assert publication["complete"] is True
        assert publication["deferred_cases"] == []
    else:
        assert {result["case"]["configuration"] for result in verdict["cases"]} == {
            "full-server-code",
            "full-server-vector-code",
        }
        assert all(result["case"]["backend"] == "seekdb" for result in verdict["cases"])
        assert not (root / "accepted/publication.json").exists()
        plan["release"] = True
        plan["release_notes"] = gate.support_text(policy, "en")
        parallel.write_json(prepared / "plan.json", plan)
        with pytest.raises(gate.GateError, match="cannot authorize publication"):
            parallel.collect(root, prepared, reports, root / "release-rejected")
    support = gate.support_text(policy, "en")
    assert "| seekdb | full-server-code |" in support
    assert "| seekdb | full-server-vector-code |" in support
    assert "| oceanbase | full-server-vector-code |" in support


@pytest.mark.parametrize("parallel_reports", ["automatic"], indirect=True)
@pytest.mark.parametrize("broken", ["missing", "failed", "skipped"])
@pytest.mark.parametrize("configuration", ["full-server-no-vector", "full-server-code", "full-server-vector-code"])
def test_automatic_acceptance_rejects_missing_failed_or_skipped_seekdb_cases(parallel_reports, broken, configuration):
    root, prepared, reports, _ = parallel_reports
    path = next(
        path
        for path in reports.glob("*/result.json")
        if json.loads(path.read_text())["result"]["case"]["backend"] == "seekdb"
        and json.loads(path.read_text())["result"]["case"]["configuration"] == configuration
    )
    if broken == "missing":
        path.unlink()
    elif broken == "failed":
        result = json.loads(path.read_text())
        result["status"] = "failed"
        parallel.write_json(path, result)
    else:
        junit = path.parent / "acceptance/junit.xml"
        tree = ElementTree.parse(junit)  # noqa: S314 -- locally generated test fixture
        ElementTree.SubElement(next(tree.iter("testcase")), "skipped")
        tree.write(junit)
    with pytest.raises(gate.GateError):
        parallel.collect(root, prepared, reports, root / "accepted")
    assert not (root / "accepted/validated-dist").exists()


@pytest.mark.parametrize("parallel_reports", ["seekdb-code"], indirect=True)
def test_diagnostic_workers_reject_unselected_case_execution(parallel_reports):
    root, prepared, _reports, _policy = parallel_reports
    plan = json.loads((prepared / "plan.json").read_text())
    index = next(i for i, case in enumerate(plan["resolved_matrix"]["required_cases"]) if case["backend"] != "seekdb")
    with pytest.raises(gate.GateError, match="Unselected"):
        parallel.execute_case(root, prepared, index, root / "unexpected-case")
    assert not (root / "unexpected-case").exists()


def test_serial_publication_gate_requires_all_twelve_cases(release):
    root, wheel, policy = release
    policy["automatic_sources"]["profiles"] = [
        {"backend": backend, "configuration": configuration, "suite": "tests/release_upgrades/test_example.py"}
        for backend in ("sqlite", "seekdb", "oceanbase")
        for configuration in (
            "full-server-no-vector",
            "full-server-vector",
            "full-server-code",
            "full-server-vector-code",
        )
    ]
    policy["mode"] = "supported-upgrades"
    parallel.write_json(root / gate.MANIFEST, policy)
    gate.check_docs(root, policy, write=True)
    gate.gate(root, wheel, root / "serial", release=True, release_notes=gate.support_text(policy, "en"))
    result = json.loads((root / "serial/result.json").read_text())
    assert len(result["cases"]) == 12
    assert result["deferred_cases"] == []
    assert result["complete"] is True
    assert result["status"] == "passed"


@pytest.mark.parametrize("configuration", ["typo"])
def test_unknown_configuration_fails_before_opening_database(tmp_path, configuration):
    work = tmp_path / "must-not-be-created"
    suite = gate.ROOT / "tests/release_upgrades/test_full_server.py"
    environment = dict(os.environ)
    environment.update(
        PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
        PC_UPGRADE_CONFIGURATION=configuration,
        PC_UPGRADE_SOURCE_VERSION="1.3.0",
        PC_UPGRADE_SOURCE_PYTHON=str(tmp_path / "nonexistent-source"),
        PC_UPGRADE_TARGET_PYTHON=str(tmp_path / "nonexistent-target"),
        PC_UPGRADE_BACKEND="sqlite",
        PC_UPGRADE_WORK=str(work),
        PC_UPGRADE_REPORT_DIR=str(tmp_path / "report"),
    )
    environment.pop("PYTEST_ADDOPTS", None)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-c",
            os.devnull,
            "--confcutdir",
            str(suite.parent),
            "-p",
            "no:cacheprovider",
            "-k",
            "test_source_release_database",
            "-q",
            str(suite),
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode != 0
    assert "Unregistered acceptance configuration" in result.stdout
    assert not work.exists()


@pytest.mark.skipif(sys.platform == "linux", reason="Linux supports the required process ownership")
def test_full_acceptance_rejects_unsupported_host_before_installing(tmp_path):
    with pytest.raises(gate.GateError, match="requires Linux"):
        gate.run_case(tmp_path, tmp_path / "wheel", {}, tmp_path / "report")
    assert not (tmp_path / "report").exists()


@pytest.mark.parametrize("configuration", [None, "different-profile"])
def test_parallel_collection_rejects_unproven_configuration(parallel_reports, configuration):
    root, prepared, reports, _ = parallel_reports
    path = reports / "case-0/acceptance/backend.json"
    backend = json.loads(path.read_text())
    if configuration is None:
        del backend["configuration"]
    else:
        backend["configuration"] = configuration
    parallel.write_json(path, backend)
    with pytest.raises(gate.GateError, match="Configuration evidence"):
        parallel.collect(root, prepared, reports, root / "accepted")
    assert not (root / "accepted/validated-dist").exists()
