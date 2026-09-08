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


"""Evaluator-owned development pairing CLI. Formal tasks are deliberately inadmissible."""

# ruff: noqa: TRY003
from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from powercontext.http import ArtifactAddress
from powercontext_datus.evaluate import evaluate_case, model_metrics
from powercontext_datus.freeze import IntegrityError, digest_json, snapshot
from powercontext_datus.report import summarize
from powercontext_datus.sandbox import Sandbox

ARMS = ("native", "enhanced")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)


def read_secret(path: str) -> str:
    ref = Path(path)
    info = ref.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise IntegrityError("secret reference must be an owned regular file with no group/other access")
    value = ref.read_text().strip()
    if not value:
        raise IntegrityError("empty task-authorized secret reference")
    return value


def file_hash(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise IntegrityError("expected a regular evaluator-owned file")
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def runtime_files(root: Path) -> str:
    # Cache redirection/no-write flags do not disable sourceless imports or
    # explicit loaders. Hash every file, including bytecode and cache-named
    # directories, plus resolved venv symlink bytes.
    if not stat.S_ISDIR(root.lstat().st_mode):
        raise IntegrityError("runtime root must be a regular directory")
    inventory = {}
    for path in sorted(root.rglob("*")):
        mode = path.lstat().st_mode
        # lstat must precede directory exclusions: rglob does not follow
        # directory symlinks, but the mounted interpreter could resolve them.
        if stat.S_ISLNK(mode) and path.is_dir():
            target = path.resolve()
            if not target.is_relative_to(root.resolve()) or "__pycache__" in target.parts:
                raise IntegrityError("runtime directory symlink escapes the inventoried tree")
            # Standard venv lib64 -> lib aliases are allowed only because their
            # targets' files are independently inventoried below the same root.
            inventory[path.relative_to(root).as_posix()] = {
                "directory_symlink": os.readlink(path),
                "target": str(target),
                "mode": stat.S_IMODE(mode),
            }
            continue
        if stat.S_ISDIR(mode):
            continue
        resolved = path.resolve()
        if not resolved.is_file():
            raise IntegrityError("runtime contains a special file")
        inventory[path.relative_to(root).as_posix()] = {
            "sha256": file_hash(resolved),
            "mode": stat.S_IMODE(path.stat().st_mode),
            "symlink": str(resolved) if path.is_symlink() else None,
        }
    return digest_json(inventory)


def input_identity(plan: dict[str, Any], sandbox: Sandbox) -> dict[str, Any]:
    result = {
        "execution": sandbox.execution_identity(),
        "plan": digest_json(plan),
        "common": file_hash(Path(plan["common_file"])),
        "oracle": file_hash(Path(plan["oracle_file"])),
        "admission": file_hash(Path(plan["admission_file"])),
        "skills": {arm: snapshot(Path(plan["arms"][arm]["skill_root"])) for arm in ARMS},
        "bridge": runtime_files(sandbox.bridge),
        "runtime": runtime_files(sandbox.python.parent.parent),
        "python": runtime_files(sandbox.python.resolve().parent.parent),
    }
    if plan.get("database_file"):
        result["database"] = file_hash(Path(plan["database_file"]))
    return result


def validate_plan(plan: dict[str, Any]) -> None:
    if plan["evidence_kind"] not in {"component_fixture", "independent_development"}:
        raise IntegrityError("only independent development or component fixtures are admitted")
    task_ids = [task["task_id"] for task in plan["tasks"]]
    if not task_ids or len(set(task_ids)) != len(task_ids):
        raise IntegrityError("nonempty unique task roster required")
    if any(set(task) != {"task_id", "question"} or not all(task.values()) for task in plan["tasks"]):
        raise IntegrityError("worker tasks must contain only task_id/question")
    if set(plan["arms"]) != set(ARMS):
        raise IntegrityError("both paired arms are required")
    validate_public(plan)
    validate_admission(plan)


def validate_public(plan: dict[str, Any]) -> None:
    public = plan["public"]
    if not 1 <= public["max_turns"] <= 50 or not 0 < plan["timeout_seconds"] <= 3600:
        raise IntegrityError("explicit bounded shared budget required")
    if set(public["model"]) - {"type", "model", "base_url", "temperature", "top_p", "reasoning_effort"}:
        raise IntegrityError("model configuration must not contain credentials or untracked extensions")
    if set(public) != {"model", "database", "max_turns", "current_date"}:
        raise IntegrityError("unknown effective configuration")
    if public["model"]["type"] != "openai":
        raise IntegrityError("unsupported model adapter")
    if plan["evidence_kind"] != "component_fixture" and (
        plan.get("database_file") or public["database"]["type"] != "mysql"
    ):
        raise IntegrityError("live admission uses the agreed MySQL/OceanBase dataset")
    endpoint = urlsplit(public["model"]["base_url"])
    if not endpoint.hostname or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment:
        raise IntegrityError("model endpoint must be explicit and contain no credentials/query/fragment")
    if endpoint.scheme != "https" and not (
        plan["evidence_kind"] == "component_fixture" and endpoint.hostname in {"127.0.0.1", "localhost"}
    ):
        raise IntegrityError("live model transport requires HTTPS")
    allowed_db = {"type", "name", "host", "port", "username"}
    if set(public["database"]) - allowed_db:
        raise IntegrityError("database credentials must use protected references")


def validate_admission(plan: dict[str, Any]) -> None:
    admission = read_json(Path(plan["admission_file"]))
    if admission["roster_sha256"] != digest_json(plan["tasks"]):
        raise IntegrityError("independent source approval does not cover this roster")
    if admission["common_sha256"] != file_hash(Path(plan["common_file"])):
        raise IntegrityError("independent source approval does not cover common material")
    oracles = read_json(Path(plan["oracle_file"]))
    if set(oracles) != {task["task_id"] for task in plan["tasks"]}:
        raise IntegrityError("oracle must retain the full fixed roster")
    if admission["oracle_sha256"] != file_hash(Path(plan["oracle_file"])):
        raise IntegrityError("oracle evidence mismatch")
    if plan["evidence_kind"] == "component_fixture":
        if admission.get("evidence_kind") != "component_fixture":
            raise IntegrityError("fixture admission must stay explicitly synthetic")
        return
    # This document is supplied by the independent evaluator, not generated or
    # self-approved by this process. Digests bind its decisions; they cannot prove
    # the truth of provenance, DB immutability or human authorization.
    validate_live_admission(plan, admission)


def validate_live_admission(plan: dict[str, Any], admission: dict[str, Any]) -> None:
    required = (
        "independent_author",
        "independent_reviewer",
        "source_receipt",
        "exposure_ledger",
        "native_learning_receipts",
        "powercontext_generation_receipt",
        "skill_approval_receipt",
        "data_version_receipt",
        "oracle_consistency_receipt",
        "service_authorization_receipt",
    )
    if any(not admission.get(key) for key in required):
        raise IntegrityError("independent evaluator must supply outstanding admission evidence")
    if admission["independent_author"] == admission["independent_reviewer"]:
        raise IntegrityError("sample provenance requires independent review")
    if admission.get("data_mode") != "immutable_read_only_snapshot":
        raise IntegrityError("a proved immutable common database version is required")
    if admission.get("evidence_kind") != "independent_development":
        raise IntegrityError("formal admission is not accepted")
    for key in required[2:]:
        receipt = admission[key]
        if not isinstance(receipt, dict) or set(receipt) != {"file", "sha256"}:
            raise IntegrityError("admission receipts must refer to actual independently reviewed evidence files")
        if file_hash(Path(receipt["file"])) != receipt["sha256"]:
            raise IntegrityError("admission evidence file digest mismatch")
    validate_deliveries(plan, admission)


def validate_deliveries(plan: dict[str, Any], admission: dict[str, Any]) -> None:
    for arm in ARMS:
        refs = admission["skill_deliveries"][arm]
        for ref in refs:
            if arm == "enhanced":
                validate_delivery_reference(ref)
            if snapshot(Path(plan["arms"][arm]["skill_root"]) / ref["name"]) != ref["files"]:
                raise IntegrityError("installed exact Skill differs from approved delivery")
        if sorted(ref["name"] for ref in refs) != sorted(plan["arms"][arm]["skill_names"]):
            raise IntegrityError("approved Skill inventory differs")
    if not admission["skill_deliveries"]["enhanced"]:
        raise IntegrityError("enhanced arm requires an independently approved exact Skill")


def validate_delivery_reference(ref: dict[str, Any]) -> None:
    try:
        address = ArtifactAddress.model_validate({"scope_id": ref.get("scope_id"), "artifact": ref.get("artifact")})
    except (ValueError, TypeError) as error:
        raise IntegrityError("enhanced Skill requires exact SDK delivery receipt") from error
    if address.artifact.family != "skill" or not ref.get("tree_digest") or not ref.get("archive_digest"):
        raise IntegrityError("enhanced Skill requires exact SDK delivery receipt")


def credentials_for(plan: dict[str, Any]) -> dict[str, str]:
    if plan["evidence_kind"] == "component_fixture":
        return {"model_api_key": "component-fixture-only"}
    refs = plan["secret_refs"]
    if set(refs) != {"model_api_key", "db_password"}:
        raise IntegrityError("explicit task-scoped model/database secret references required")
    return {key: read_secret(path) for key, path in refs.items()}


def run_one(
    plan: dict[str, Any],
    sandbox: Sandbox,
    arm: str,
    task: dict[str, str],
    *,
    run_id: str,
    effective: str | None = None,
    prepare: bool = False,
    credentials: dict[str, str] | None = None,
) -> dict[str, Any]:
    request = {
        "run_id": run_id,
        "attempt_id": str(uuid.uuid4()),
        "task": task,
        "public": plan["public"],
        "skill_names": plan["arms"][arm]["skill_names"],
        "credentials": dict(credentials if credentials is not None else credentials_for(plan)),
        "evidence_kind": plan["evidence_kind"],
        "denied_paths": [
            plan["admission_file"],
            *([plan["oracle_file"]] if plan.get("oracle_file") else []),
            *plan.get("denied_paths", []),
            "/proc/self/environ",
            "/proc/1/root",
            "/proc/self/fd",
        ],
        "prepare_only": prepare,
        "effective_sha256": effective,
    }
    return sandbox.run(
        request,
        common=Path(plan["common_file"]),
        skills=Path(plan["arms"][arm]["skill_root"]),
        database=Path(plan["database_file"]) if plan.get("database_file") else None,
        network=True,
        timeout=plan["timeout_seconds"],
    )


def freeze_plan(plan: dict[str, Any], sandbox: Sandbox) -> dict[str, Any]:
    plan = json.loads(json.dumps(plan))
    validate_plan(plan)
    before = input_identity(plan, sandbox)
    sandbox = sandbox.bind_execution(before["execution"])
    credentials = credentials_for(plan)
    effective = {}
    preparation = {}
    for arm in ARMS:
        verify_inputs(plan, sandbox, before)
        run = run_one(
            plan,
            sandbox,
            arm,
            {"task_id": "prepare", "question": ""},
            run_id=str(uuid.uuid4()),
            prepare=True,
            credentials=credentials,
        )
        configs = [r for r in run["records"] if r["kind"] == "effective_config"]
        verify_inputs(plan, sandbox, before)
        if run["returncode"] != 0 or run["malformed_output"] or run.get("control_failure") or len(configs) != 1:
            raise IntegrityError("native effective freeze failed; inspect with component probes")
        effective[arm] = configs[0]["effective_sha256"]
        preparation[arm] = run
    verify_inputs(plan, sandbox, before)
    return {
        "version": 2,
        "plan": plan,
        "inputs": before,
        "effective": effective,
        "preparation": preparation,
        "formal_state": "not_started",
    }


def verify_inputs(plan: dict[str, Any], sandbox: Sandbox, expected: dict[str, Any]) -> None:
    if input_identity(plan, sandbox) != expected:
        raise IntegrityError("frozen manifest/inputs drifted")


def run_pair(manifest: dict[str, Any], sandbox: Sandbox, output: Path) -> dict[str, Any]:
    manifest = json.loads(json.dumps(manifest))
    plan = manifest["plan"]
    validate_plan(plan)
    if manifest["version"] != 2 or "execution" not in manifest["inputs"]:
        raise IntegrityError("manifest requires a frozen execution root; freeze again")
    sandbox = sandbox.bind_execution(manifest["inputs"]["execution"])
    verify_inputs(plan, sandbox, manifest["inputs"])
    # Read once per invocation, share only in memory, and send a private copy
    # to each worker. Never hash or serialize the secret values as identity.
    credentials = credentials_for(plan)
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    run_id = str(uuid.uuid4())
    write_json(output / "manifest.json", manifest)
    oracles = read_json(Path(plan["oracle_file"]))
    verdicts: dict[str, list[Any]] = {arm: [] for arm in ARMS}
    evidence = []
    abort = None
    # Both arms finish before evaluator-owned feedback is produced. Nothing in
    # output is mounted in any subsequent worker.
    for task in plan["tasks"]:
        for arm in ARMS:
            result = unstarted_result(abort or "leakage/state_drift")
            if abort is None:
                try:
                    verify_inputs(plan, sandbox, manifest["inputs"])
                    result = run_one(
                        plan,
                        sandbox,
                        arm,
                        task,
                        run_id=run_id,
                        effective=manifest["effective"][arm],
                        credentials=credentials,
                    )
                    abort = result.get("control_failure")
                    verify_inputs(plan, sandbox, manifest["inputs"])
                except (IntegrityError, OSError) as error:
                    abort = "leakage/state_drift" if isinstance(error, IntegrityError) else "environment/auth"
                    result.update(state_valid=False, control_failure=abort)
            index = len(evidence)
            evidence.append((arm, task["task_id"], result))
            write_json(output / f"case-{index:04d}.json", {"arm": arm, "task_id": task["task_id"], **result})
    try:
        validate_plan(plan)
        verify_inputs(plan, sandbox, manifest["inputs"])
        state_valid = abort is None
    except (IntegrityError, OSError):
        state_valid = False
    task_ids = [task["task_id"] for task in plan["tasks"]]
    details = []
    for arm, task_id, run in evidence:
        verdict, detail = evaluate_case(
            task_id, run, oracles[task_id], state_valid=state_valid, data_version_verified=True
        )
        verdicts[arm].append(verdict)
        detail["metrics"] = model_metrics(run["records"], plan.get("rates"))
        details.append({"arm": arm, "task_id": task_id, "verdict": asdict(verdict), **detail})
    live = plan["evidence_kind"] == "independent_development"
    report = {
        "evidence_kind": plan["evidence_kind"],
        "run_id": run_id,
        "formal_state": "not_started",
        "real_learning_runs": 0,
        "real_development_runs": {
            arm: sum(
                a == arm and any(r["kind"] == "question_injected" for r in run["records"]) for a, _, run in evidence
            )
            if live
            else 0
            for arm in ARMS
        },
        "component_case_runs": len(evidence) if not live else 0,
        "state_valid": state_valid,
        "arms": {arm: summarize_arm(task_ids, verdicts[arm], state_valid=state_valid, live=live) for arm in ARMS},
        "cases": details,
    }
    write_json(output / "report.json", report)
    return report


def summarize_arm(task_ids, verdicts, *, state_valid: bool, live: bool) -> dict[str, Any]:
    result = summarize(task_ids, verdicts, data_version_verified=state_valid)
    if not live:
        result["component_pass"] = result["accepted"]
        result["accepted"] = False
    return result


def unstarted_result(reason: str) -> dict[str, Any]:
    return {
        "returncode": None,
        "records": [],
        "malformed_output": False,
        "timeout": False,
        "control_failure": reason,
        "not_started": True,
        "process_started": False,
        "state_valid": False,
        "wall_seconds": None,
        "stderr_bytes": 0,
    }


def run_samples(plan: dict[str, Any], sandbox: Sandbox, output: Path) -> dict[str, Any]:
    """Generate native learning receipts before Skill generation/approval exists.

    These are execution receipts, not independently validated lessons. The
    evaluator verifies them before calling learning.generate_from_examples.
    """
    if plan["evidence_kind"] != "independent_learning":
        raise IntegrityError("sample entry requires independent learning provenance")
    validate_public(plan)
    tasks = plan["tasks"]
    if not tasks or len({task["task_id"] for task in tasks}) != len(tasks):
        raise IntegrityError("unique independent sample roster required")
    if any(set(task) != {"task_id", "question"} for task in tasks):
        raise IntegrityError("sample worker receives only task_id/question")
    admission = read_json(Path(plan["admission_file"]))
    if (
        admission.get("roster_sha256") != digest_json(tasks)
        or admission.get("common_sha256") != file_hash(Path(plan["common_file"]))
        or admission.get("evidence_kind") != "independent_learning"
        or not admission.get("independent_author")
        or not admission.get("independent_reviewer")
        or admission["independent_author"] == admission["independent_reviewer"]
    ):
        raise IntegrityError("independently reviewed sample/common provenance required")
    for name in ("source_receipt", "exposure_ledger", "service_authorization_receipt", "data_version_receipt"):
        ref = admission[name]
        if file_hash(Path(ref["file"])) != ref["sha256"]:
            raise IntegrityError("sample admission evidence mismatch")
    credentials = credentials_for(plan)
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    write_json(output / "plan.json", plan)
    run_id = str(uuid.uuid4())
    results = []
    abort = None
    for index, task in enumerate(tasks):
        run = (
            run_one(plan, sandbox, "native", task, run_id=run_id, credentials=credentials)
            if abort is None
            else unstarted_result(abort)
        )
        write_json(output / f"sample-{index:04d}.json", {"task_id": task["task_id"], **run})
        results.append(run)
        if run.get("control_failure"):
            abort = run["control_failure"]
    report = {
        "evidence_kind": "independent_learning",
        "formal_state": "not_started",
        "real_learning_runs": sum(any(r["kind"] == "question_injected" for r in v["records"]) for v in results),
        "native_completed": sum(v["returncode"] == 0 and not v.get("control_failure") for v in results),
        "state_valid": abort is None,
        "independent_lesson_validation": "required",
        "skill_generation": "not_started",
    }
    write_json(output / "report.json", report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["sample", "freeze", "run"])
    parser.add_argument("--input", type=Path, required=True, help="Evaluator-owned plan (freeze) or manifest (run)")
    parser.add_argument("--output", type=Path, required=True, help="New manifest file or new evidence directory")
    parser.add_argument("--runtime-python", type=Path, required=True)
    args = parser.parse_args()
    sandbox = Sandbox(args.runtime_python.absolute(), Path(__file__).resolve().parents[1])
    data = read_json(args.input)
    if args.command == "sample":
        run_samples(data, sandbox, args.output)
    elif args.command == "freeze":
        write_json(args.output, freeze_plan(data, sandbox))
    else:
        run_pair(data, sandbox, args.output)


if __name__ == "__main__":
    main()
