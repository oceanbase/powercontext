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

"""Gate upgrade claims on this build's complete, installed-release acceptance."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import tomllib
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from email.parser import Parser
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from upgrade_release_matrix import STABLE, resolve_matrix

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = Path("release/upgrade-support.json")
MANAGED_INPUTS = (
    "src/powercontext/builtin/persistence",
    "src/powercontext/builtin/code",
    "src/powercontext/server/identity.py",
    "src/powercontext/server/authz",
    "src/powercontext/limits.py",
)
PHASES = frozenset({
    "test_source_release_database",
    "test_public_migration_commands",
    "test_legacy_startup_is_rejected",
    "test_repeated_migration",
    "test_target_startup",
    "test_read_write_and_retrieval",
    "test_authorization",
    "test_candidates_and_processing",
    "test_background_processing",
    "test_preserved_history_and_cursors",
    "test_fresh_upgrade_equivalence",
})
VERSION = r"\d+\.\d+\.\d+(?:(?:a|b|rc)\d+)?"
NOTICE = {
    "en": "Upgrades from 1.3.0 and 1.4.0 require passing full Server acceptance on SQLite, seekDB and OceanBase. These migration paths are not yet verified; do not upgrade existing databases.",
    "zh": "1.3.0 和 1.4.0 升级必须通过 SQLite、seekDB 和 OceanBase 的完整 Server 验收。当前迁移路径尚未验证，请勿升级已有数据库。",  # noqa: RUF001 -- Chinese prose
}


class GateError(ValueError):
    """An upgrade claim has no valid declaration or passing acceptance."""


def require(condition: Any, message: str) -> None:
    if not condition:
        raise GateError(message)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def managed_digest(root: Path) -> str:
    """Bind the review declaration to all persistence inputs, not a new-file count.

    This deliberately over-approximates schema changes. It is a review trigger,
    not a claim to prove SQL or data-format compatibility from Python syntax.
    Normalize checkout line endings; artifact hashes must retain their exact bytes.
    """
    inputs = [root / relative for relative in MANAGED_INPUTS]
    files = {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
        for location in inputs
        for path in (location.rglob("*") if location.is_dir() else [location])
        if path.is_file() and path.suffix in {".py", ".sql", ".json"}
    }
    require(files, "No managed persistence inputs found")
    return hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()


def load_policy(root: Path) -> dict[str, Any]:
    policy = json.loads((root / MANIFEST).read_text(encoding="utf-8"))
    require(
        set(policy) == {"format", "target_version", "mode", "schema_review", "cases", "automatic_sources"},
        "Invalid policy fields",
    )
    require(policy["format"] == 2, "Unsupported upgrade policy format")
    contract = (root / "openapi/powercontext.yaml").read_text(encoding="utf-8")
    match = re.search(rf"^  version: ({VERSION})$", contract, re.MULTILINE)
    require(
        match is not None and match[1] == policy["target_version"], "Upgrade policy target differs from API release"
    )
    require(policy["mode"] in {"upgrade-required", "supported-upgrades"}, "Unknown upgrade policy mode")
    review = policy["schema_review"]
    require(set(review) == {"persistence_sha256", "rationale"}, "Invalid schema review fields")
    require(
        isinstance(review["rationale"], str) and review["rationale"].strip(), "Schema compatibility rationale required"
    )
    require(
        review["persistence_sha256"] == managed_digest(root),
        "Persistence changed (file set or content; CRLF/LF normalized): update the compatibility review",
    )
    cases = policy["cases"]
    require(isinstance(cases, list), "Upgrade cases must be a list")
    automatic = policy["automatic_sources"]
    profiles = []
    if automatic is not None:
        require(set(automatic) == {"strategy", "source_version", "profiles"}, "Invalid automatic source policy")
        require(automatic["strategy"] == "fixed-version", "Unknown source selection strategy")
        require(
            isinstance(automatic["source_version"], str) and re.fullmatch(STABLE, automatic["source_version"]),
            "Fixed source must be a stable version",
        )
        profiles = automatic["profiles"]
        require(isinstance(profiles, list) and profiles, "Automatic sources require backend profiles")
        for profile in profiles:
            require(set(profile) == {"backend", "configuration", "suite"}, "Invalid automatic backend profile")
    require(bool(cases or profiles), "Upgrade acceptance requires a nonempty matrix")
    required_backends = {"sqlite", "seekdb", "oceanbase"}
    covered = {case["backend"] for case in cases if case.get("source_version") == "1.3.0"} | (
        {profile["backend"] for profile in profiles} if automatic and automatic["source_version"] == "1.3.0" else set()
    )
    require(covered >= required_backends, "The 1.3.0 upgrade baseline requires SQLite, seekDB and OceanBase")
    identities: set[tuple[str, str, str]] = set()
    for case in cases:
        require(set(case) == {"source_version", "backend", "configuration", "suite"}, "Invalid upgrade case fields")
        require(
            isinstance(case["source_version"], str) and re.fullmatch(VERSION, case["source_version"]),
            "Pin a source release",
        )
    for case in [*cases, *profiles]:
        require(case["backend"] in {"sqlite", "seekdb", "oceanbase"}, "Unknown backend")
        require(re.fullmatch(r"[a-z][a-z0-9-]*", case["configuration"]), "Configuration needs a stable identifier")
        identity = (case.get("source_version", "automatic"), case["backend"], case["configuration"])
        require(identity not in identities, "Duplicate upgrade combination")
        identities.add(identity)
        require(
            isinstance(case["suite"], str) and case["suite"],
            "A real acceptance suite is required for every backend profile",
        )
        suite = (root / case["suite"]).resolve()
        require(
            suite.is_relative_to((root / "tests/release_upgrades").resolve()),
            "Suite must be under tests/release_upgrades",
        )
        require(
            suite.is_file() and suite.name.startswith("test_") and suite.suffix == ".py", "Missing acceptance suite"
        )
    return policy


def support_text(policy: dict[str, Any], language: str) -> str:
    if policy["mode"] == "upgrade-required":
        return NOTICE[language]
    title = (
        "Supported database upgrades (other combinations are unsupported):"
        if language == "en"
        else "支持的数据库升级组合（其他组合不受支持）："  # noqa: RUF001 -- Chinese prose
    )
    lines = [title, "", "| Source | Target | Backend | Configuration |", "| --- | --- | --- | --- |"]
    lines.extend(
        f"| {case['source_version']} | {policy['target_version']} | {case['backend']} | {case['configuration']} |"
        for case in policy["cases"]
    )
    if automatic := policy["automatic_sources"]:
        lines.extend(
            f"| {automatic['source_version']} | {policy['target_version']} | {profile['backend']} | {profile['configuration']} |"
            for profile in automatic["profiles"]
        )
    return "\n".join(lines)


def check_docs(root: Path, policy: dict[str, Any], *, write: bool = False) -> None:
    for language in ("en", "zh"):
        path = root / f"docs/{language}/docs/get-started/install-and-run.md"
        content = path.read_text(encoding="utf-8")
        pattern = r"(?<=<!-- upgrade-support:start -->\n).*?(?=\n<!-- upgrade-support:end -->)"
        matches = list(re.finditer(pattern, content, re.DOTALL))
        require(len(matches) == 1, f"{path.name}: exactly one upgrade-support region required")
        expected = support_text(policy, language)
        if write:
            match = matches[0]
            path.write_text(content[: match.start()] + expected + content[match.end() :], encoding="utf-8")
        else:
            require(matches[0][0] == expected, f"{language} upgrade support instructions differ from the policy")


def validate_junit(report: Path) -> None:
    tree = ElementTree.parse(report)  # noqa: S314 -- locally produced acceptance report
    cases = list(tree.iter("testcase"))
    require(cases, "No acceptance tests executed")
    require(not any(list(tree.iter(tag)) for tag in ("failure", "error", "skipped")), "Acceptance failed or skipped")
    names = {case.attrib.get("name", "").split("[", 1)[0] for case in cases}
    require(names >= PHASES, f"Missing full Server acceptance phases: {sorted(PHASES - names)}")


def validate_backend_evidence(path: Path, backend: str) -> dict[str, Any]:
    evidence = json.loads(path.read_text(encoding="utf-8"))
    require(evidence.get("backend") == backend, "Backend evidence does not match the declared case")
    for key in ("engine_version", "driver_version", "source_schema", "target_schema", "fresh_schema"):
        require(isinstance(evidence.get(key), str) and evidence[key].strip(), f"Missing backend evidence: {key}")
    if backend == "oceanbase":
        require(evidence.get("tenant_mode") == "mysql", "OceanBase MySQL tenant evidence required")
        require(
            isinstance(evidence.get("proxy_version"), str) and evidence["proxy_version"],
            "Record proxy version or direct",
        )
    return evidence


def run(command: list[str], *, environment: dict[str, str], cwd: Path, log: Path) -> None:
    with log.open("w", encoding="utf-8") as output:
        result = subprocess.run(  # noqa: S603 -- fixed tools and reviewed repository test paths, no shell
            command, env=environment, cwd=cwd, stdout=output, stderr=subprocess.STDOUT, timeout=1800, check=False
        )
    require(result.returncode == 0, f"Command failed; inspect {log.name}")


def run_owned(
    command: list[str],
    *,
    environment: dict[str, str],
    cwd: Path,
    log: Path,
    timeout: float = 1800,
    shutdown_timeout: float = 30,
) -> None:
    require(sys.platform == "linux", "Installed-release acceptance requires Linux process ownership and cleanup")
    supervised = [
        sys.executable,
        str(Path(__file__).with_name("upgrade_process.py")),
        "--parent-pid",
        str(os.getpid()),
        "--timeout",
        str(timeout),
        "--shutdown-timeout",
        str(shutdown_timeout),
        "--",
        *command,
    ]
    # The supervisor enforces the deadline and reaps every owned descendant,
    # including detached native engines, before workspace cleanup can run.
    with (
        log.open("w", encoding="utf-8") as output,
        subprocess.Popen(  # noqa: S603 -- fixed gate supervisor and reviewed command, no shell
            supervised, env=environment, cwd=cwd, stdout=output, stderr=subprocess.STDOUT, start_new_session=True
        ) as process,
    ):
        try:
            returncode = process.wait()
        except BaseException:
            # Repeated interrupts must not release the workspace before the
            # owner has finished closing descendants.
            previous = signal.signal(signal.SIGINT, signal.SIG_IGN)
            try:
                process.terminate()
                process.wait()
            finally:
                signal.signal(signal.SIGINT, previous)
            raise
    require(returncode == 0, f"Command failed; inspect {log.name}")


def installed(python: Path, environment: dict[str, str], cwd: Path) -> dict[str, str]:
    result = subprocess.run(  # noqa: S603 -- isolated environment interpreter
        [
            str(python),
            "-I",
            "-c",
            "import json,platform; from importlib.metadata import version; "
            "print(json.dumps({'powercontext': version('powercontext'), 'python': platform.python_version()}))",
        ],
        env=environment,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    return json.loads(result.stdout)


def wheel_version(wheel: Path) -> str:
    with zipfile.ZipFile(wheel) as archive:
        names = [name for name in archive.namelist() if name.endswith(".dist-info/METADATA")]
        require(len(names) == 1, "Expected exactly one wheel distribution")
        metadata = Parser().parsestr(archive.read(names[0]).decode())
    require(metadata["Name"] == "powercontext", "Expected a PowerContext wheel")
    return str(metadata["Version"])


def _preserve_seekdb_logs(directory: Path, output: Path) -> None:
    """Retain bounded diagnostics from synthetic acceptance stores, excluding database files."""

    remaining = 16 * 1024 * 1024
    files = []
    # Preserve the existing revision journal before native logs can use the budget.
    paths = [
        path
        for name in ("database", "fresh")
        for path in sorted((directory / f"{name}.pc-migration-state").glob("*/maintenance.json"))
    ]
    paths += [path for name in ("database", "fresh") for path in sorted((directory / name).rglob("*.log*"))]
    with zipfile.ZipFile(output / "seekdb-native-logs.zip", "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in paths:
            if remaining == 0:
                break
            if path.is_symlink() or not path.is_file():
                continue
            relative = path.relative_to(directory).as_posix()
            segments = []
            captured = 0
            with path.open("rb") as source:
                size = source.seek(0, os.SEEK_END)
                length = min(size, 4 * 1024 * 1024, remaining)
                if length < size:
                    head = length // 2
                    ranges = [
                        (f"head/{relative}", 0, head),
                        (f"tail/{relative}", size - (length - head), length - head),
                    ]
                else:
                    ranges = [(relative, 0, length)]
                for entry, offset, count in ranges:
                    if count == 0 and size:
                        continue
                    source.seek(offset)
                    content = source.read(count)
                    archive.writestr(entry, content)
                    remaining -= len(content)
                    captured += len(content)
                    segments.append({"path": entry, "offset_bytes": offset, "captured_bytes": len(content)})
            files.append({
                "path": relative,
                "size_bytes": size,
                "captured_bytes": captured,
                "truncated": captured < size,
                "segments": segments,
            })
    usage = shutil.disk_usage(directory)
    manifest = {
        "files": files,
        "budget_exhausted": remaining == 0,
        "filesystem": {"total_bytes": usage.total, "used_bytes": usage.used, "free_bytes": usage.free},
    }
    (output / "seekdb-native-logs.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


@contextmanager
def case_workspace(backend: str, output: Path) -> Iterator[Path]:
    # Keep embedded sockets under a short private path. Native logs must leave
    # this directory before cleanup, including when acceptance raises or times out.
    with tempfile.TemporaryDirectory(prefix="pc-up-", dir="/tmp" if os.name != "nt" else None) as temporary:  # noqa: S108 -- private mkdtemp directory
        directory = Path(temporary)
        try:
            yield directory
        finally:
            if backend == "seekdb":
                try:
                    _preserve_seekdb_logs(directory, output)
                except OSError as error:
                    # Diagnostics must not replace the acceptance failure.
                    print(f"seekDB native log collection failed: {type(error).__name__}", file=sys.stderr)


def run_case(root: Path, wheel: Path, case: dict[str, Any], output: Path) -> dict[str, Any]:
    require(sys.platform == "linux", "Installed-release acceptance requires Linux process ownership and cleanup")
    output.mkdir(parents=True, exist_ok=False)
    # Never inherit a developer's live deployment or pytest selection overrides.
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("POWERCONTEXT_", "PYTEST_")) and key not in {"PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"}
    }
    with case_workspace(case["backend"], output) as directory:
        extras = "cli,server,seekdb" if case["backend"] == "seekdb" else "cli,server"
        if case["configuration"] in {"full-server-code", "full-server-vector-code"}:
            extras += ",code"
        interpreters = {}
        artifact = case["source_artifact"]
        packages = {
            "source": f"powercontext[{extras}] @ {artifact['url']}#sha256={artifact['sha256']}",
            "target": f"{wheel}[{extras}]",
        }
        # Installed-release environments resolve separately from the checkout.
        # Use the CI-locked engine for both the historical source and the target.
        engine_requirement = []
        if case["backend"] == "seekdb":
            lock = tomllib.loads((root / "uv.lock").read_text(encoding="utf-8"))
            binding = next(package for package in lock["package"] if package["name"] == "pylibseekdb")
            engine_requirement = [f"pylibseekdb=={binding['version']}"]
        for role, package in packages.items():
            venv = directory / role
            run_owned(
                ["uv", "venv", "--python", sys.executable, str(venv)],
                environment=environment,
                cwd=directory,
                log=output / f"{role}-venv.log",
            )
            python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            run_owned(
                ["uv", "pip", "install", "--python", str(python), package, *engine_requirement],
                environment=environment,
                cwd=directory,
                log=output / f"{role}-install.log",
            )
            interpreters[role] = python
        metadata = {role: installed(python, environment, directory) for role, python in interpreters.items()}
        require(metadata["source"]["powercontext"] == case["source_version"], "Source release mismatch")
        require(metadata["target"]["powercontext"] == wheel_version(wheel), "Target wheel version mismatch")
        (output / "installed.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
        environment.update({
            "PC_UPGRADE_REPORT_DIR": str(output),
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            "PC_UPGRADE_SOURCE_PYTHON": str(interpreters["source"]),
            "PC_UPGRADE_SOURCE_VERSION": case["source_version"],
            "PC_UPGRADE_TARGET_PYTHON": str(interpreters["target"]),
            "PC_UPGRADE_BACKEND": case["backend"],
            "PC_UPGRADE_CONFIGURATION": case["configuration"],
            "PC_UPGRADE_WORK": str(directory),
            "PC_UPGRADE_EVIDENCE": str(output / "backend.json"),
        })
        report = output / "junit.xml"
        run_owned(
            [
                sys.executable,
                "-m",
                "pytest",
                "-c",
                "/dev/null" if os.name != "nt" else "NUL",
                "--confcutdir",
                str(root / "tests/release_upgrades"),
                "--strict-markers",
                "-p",
                "no:cacheprovider",
                "-o",
                "xfail_strict=true",
                "-ra",
                f"--junitxml={report}",
                str(root / case["suite"]),
            ],
            environment=environment,
            cwd=directory,
            log=output / "acceptance.log",
        )
        backend = validate_case_output(case, output)
        return {"case": case, "installed": metadata, "database": backend, "status": "passed"}


def validate_case_output(case: dict[str, Any], output: Path) -> dict[str, Any]:
    validate_junit(output / "junit.xml")
    backend = validate_backend_evidence(output / "backend.json", case["backend"])
    require(backend.get("configuration") == case["configuration"], "Configuration evidence differs from the case")
    if case["configuration"] in {"full-server-code", "full-server-vector-code"}:
        code = backend.get("code")
        if not isinstance(code, dict):
            raise GateError("Installed Code acceptance evidence required")  # noqa: TRY003 -- operator diagnostic
        require(
            re.fullmatch(r"[0-9a-f]{64}", str(code.get("fingerprint", "")))
            and all(
                code.get(field) is True
                for field in ("immutable_cache_files_preserved", "symbol_ids_preserved", "server_query_ready")
            ),
            "The installed Code index must remain queryable with its original immutable identity",
        )
    if case["configuration"] in {"full-server-vector", "full-server-vector-code"}:
        vectors = backend.get("vectors")
        if not isinstance(vectors, dict):
            raise GateError("Installed vector acceptance evidence required")  # noqa: TRY003 -- operator diagnostic
        require(
            vectors.get("dimension") == (4 if case["configuration"].endswith("-code") else 3)
            and vectors.get("maintenance_embedding_requests") == 0
            and all(
                vectors.get(field) is True
                for field in (
                    "historical_atomic_and_topic_embeddings_preserved",
                    "historical_and_new_vector_reads",
                    "fresh_vector_read",
                    "profile_drift_rejected",
                )
            )
            and (case["backend"] != "sqlite" or vectors.get("missing_sqlite_helper_rejected") is True),
            "Installed vector acceptance must preserve and retrieve the original embedding space",
        )
    return backend


def validate_target(root: Path, wheel: Path, *, release: bool, release_notes: str | None) -> dict[str, Any]:
    policy = load_policy(root)
    check_docs(root, policy)
    actual_version = wheel_version(wheel)
    if release:
        require(actual_version == policy["target_version"], "Wheel version differs from upgrade policy target")
        require(release_notes is not None, "Published release notes must declare upgrade support")
        require(
            support_text(policy, "en") in (release_notes or ""),
            "GitHub Release notes must include the exact upgrade support declaration",
        )
    return policy


def gate(root: Path, wheel: Path, output: Path, *, release: bool = False, release_notes: str | None = None) -> None:
    policy = validate_target(root, wheel, release=release, release_notes=release_notes)
    actual_version = wheel_version(wheel)
    # A fresh output directory prevents accepting stale reports from another build.
    output.mkdir(parents=True, exist_ok=False)
    evidence: dict[str, Any] = {
        "policy_sha256": sha256(root / MANIFEST),
        "target_version": actual_version,
        "target_wheel_sha256": sha256(wheel),
        "mode": policy["mode"],
        "cases": [],
        "status": "failed",
    }
    try:
        matrix = resolve_matrix(policy, actual_version)
        evidence["resolved_matrix"] = matrix
        require(matrix["required_cases"], "No required upgrade acceptance cases")
        failures = []
        evidence["deferred_cases"] = []
        evidence["complete"] = True
        required = matrix["required_cases"]
        for index, case in enumerate(required):
            try:
                evidence["cases"].append(run_case(root, wheel, case, output / f"case-{index}"))
            except (GateError, OSError, ValueError, subprocess.SubprocessError, ElementTree.ParseError) as error:
                # A broken backend must not prevent the other required backends
                # from running and producing their own failure evidence.
                failures.append(case["backend"])
                evidence["cases"].append({"case": case, "status": "failed", "error": str(error)})
        require(not failures, f"Required upgrade acceptance failed: {', '.join(failures)}")
        if release:
            require(
                policy["mode"] == "supported-upgrades",
                "Upgrade acceptance passed; update the reviewed support declaration before publication",
            )
        evidence["status"] = "passed"
    finally:
        (output / "result.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--release", action="store_true")
    parser.add_argument("--release-notes", type=Path)
    parser.add_argument("--write-docs", action="store_true")
    args = parser.parse_args()
    try:
        policy = load_policy(ROOT)
        check_docs(ROOT, policy, write=args.write_docs)
        if args.wheel:
            require(args.output is not None, "--output is required with --wheel")
            gate(
                ROOT,
                args.wheel.resolve(),
                args.output.resolve(),
                release=args.release,
                release_notes=args.release_notes.read_text(encoding="utf-8") if args.release_notes else None,
            )
        else:
            require(not args.release, "--release requires the actual release wheel")
        print(f"Upgrade policy: {policy['mode']}; source/backend combinations are resolved against the target wheel")
    except (GateError, OSError, ValueError, subprocess.SubprocessError, ElementTree.ParseError) as error:
        print(f"Upgrade release gate failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
