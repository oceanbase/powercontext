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

"""Prepare once, execute disjoint upgrade cases, and collect the complete evidence."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tarfile
import uuid
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

import upgrade_release_gate as gate
from run_upgrade_release_gate import stage_distributions
from upgrade_release_matrix import resolve_matrix


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


SELECTIONS = ("full", "automatic", "seekdb-code")


def selected_indexes(cases: list[dict[str, Any]], selection: str, *, release: bool) -> list[int]:
    gate.require(selection in SELECTIONS, "Unknown upgrade selection")
    gate.require(not release or selection != "seekdb-code", "Manual-only acceptance cannot authorize publication")
    selected = [
        index
        for index, case in enumerate(cases)
        if selection in {"full", "automatic"}
        or (case["backend"] == "seekdb" and case["configuration"] in {"full-server-code", "full-server-vector-code"})
    ]
    gate.require(selected, "No cases match the upgrade selection")
    return selected


def prepare(
    root: Path, dist: Path, output: Path, *, release: bool, release_notes: str | None, selection: str = "full"
) -> dict[str, Any]:
    """Freeze the distributions and source discovery before scheduling any cases."""
    gate.require(not release or selection != "seekdb-code", "Manual-only acceptance cannot authorize publication")
    output.mkdir(parents=True, exist_ok=False)
    wheel, files = stage_distributions(dist, output / "inputs", release=release)
    policy = gate.validate_target(root, wheel, release=release, release_notes=release_notes)
    matrix = resolve_matrix(policy, gate.wheel_version(wheel))
    gate.require(matrix["required_cases"], "No required upgrade acceptance cases")
    plan = {
        "format": 1,
        "execution_id": uuid.uuid4().hex,
        "workflow_run_id": os.environ.get("GITHUB_RUN_ID"),
        "source_sha": os.environ.get("PC_UPGRADE_SOURCE_SHA", os.environ.get("GITHUB_SHA")),
        "policy_sha256": gate.sha256(root / gate.MANIFEST),
        "target_version": gate.wheel_version(wheel),
        "target_wheel": wheel.name,
        "artifacts": {path.name: gate.sha256(path) for path in files},
        "mode": policy["mode"],
        "release": release,
        "release_notes": release_notes,
        "resolved_matrix": matrix,
        "selection": selection,
    }
    selected = selected_indexes(matrix["required_cases"], selection, release=release)
    write_json(output / "plan.json", plan)
    return {
        "include": [
            {
                "index": index,
                "backend": case["backend"],
                "configuration": case["configuration"],
                "source_version": case["source_version"],
            }
            for index, case in enumerate(matrix["required_cases"])
            if index in selected
        ]
    }


def read_plan(root: Path, prepared: Path) -> tuple[dict[str, Any], Path]:
    plan = json.loads((prepared / "plan.json").read_text(encoding="utf-8"))
    gate.require(plan["format"] == 1, "Unknown parallel upgrade plan format")
    gate.require(plan["workflow_run_id"] == os.environ.get("GITHUB_RUN_ID"), "Evidence belongs to another workflow run")
    gate.require(
        plan["source_sha"] == os.environ.get("PC_UPGRADE_SOURCE_SHA", os.environ.get("GITHUB_SHA")),
        "Evidence belongs to another checkout",
    )
    gate.require(plan["policy_sha256"] == gate.sha256(root / gate.MANIFEST), "Upgrade policy changed after preparation")
    inputs = prepared / "inputs"
    files = list(inputs.iterdir())
    gate.require(all(path.is_file() and not path.is_symlink() for path in files), "Invalid prepared distributions")
    gate.require(
        {path.name: gate.sha256(path) for path in files} == plan["artifacts"], "Prepared distributions changed"
    )
    gate.require(plan["target_wheel"] in plan["artifacts"], "Missing prepared target wheel")
    wheel = inputs / plan["target_wheel"]
    policy = gate.validate_target(root, wheel, release=plan["release"], release_notes=plan["release_notes"])
    gate.require(
        plan["target_version"] == gate.wheel_version(wheel) and plan["mode"] == policy["mode"], "Target changed"
    )
    # Validate the frozen matrix against the reviewed policy without rediscovering
    # PyPI independently on each runner (which could select different evidence).
    declared = list(policy["cases"])
    if automatic := policy["automatic_sources"]:
        declared += [{"source_version": automatic["source_version"], **profile} for profile in automatic["profiles"]]
    required = plan["resolved_matrix"]["required_cases"]
    gate.require(
        [{key: value for key, value in case.items() if key != "source_artifact"} for case in required] == declared,
        "Prepared matrix differs from reviewed cases",
    )
    gate.require(required and plan["resolved_matrix"]["candidate_cases"] == required, "Incomplete prepared matrix")
    selected_indexes(required, plan["selection"], release=plan["release"])
    return plan, wheel


def execute_case(root: Path, prepared: Path, index: int, output: Path) -> None:
    plan, wheel = read_plan(root, prepared)
    cases = plan["resolved_matrix"]["required_cases"]
    gate.require(
        index in selected_indexes(cases, plan["selection"], release=plan["release"]), "Unselected upgrade case index"
    )
    output.mkdir(parents=True, exist_ok=False)
    evidence: dict[str, Any] = {"plan_sha256": gate.sha256(prepared / "plan.json"), "index": index, "status": "failed"}
    try:
        evidence["result"] = gate.run_case(root, wheel, cases[index], output / "acceptance")
        gate.require(evidence["result"]["status"] == "passed", "Upgrade case failed")
        read_plan(root, prepared)
        evidence["status"] = "passed"
    finally:
        # If provisioning failed before this command, the collector instead sees
        # a missing result and rejects the matrix; it cannot count diagnostics as a pass.
        write_json(output / "result.json", evidence)


def collect(root: Path, prepared: Path, reports: Path, output: Path) -> list[Path]:
    """Accept exactly one passing result per planned case, then expose the tested bytes."""
    plan, wheel = read_plan(root, prepared)
    output.mkdir(parents=True, exist_ok=False)
    evidence: dict[str, Any] = {
        "policy_sha256": plan["policy_sha256"],
        "target_version": plan["target_version"],
        "target_wheel_sha256": plan["artifacts"][wheel.name],
        "mode": plan["mode"],
        "resolved_matrix": plan["resolved_matrix"],
        "execution_id": plan["execution_id"],
        "selection": plan["selection"],
        "complete": False,
        "cases": [],
        "status": "failed",
    }
    try:
        results = list(reports.glob("*/result.json"))
        cases = plan["resolved_matrix"]["required_cases"]
        selected = selected_indexes(cases, plan["selection"], release=plan["release"])
        evidence["deferred_cases"] = [case for index, case in enumerate(cases) if index not in selected]
        gate.require(len(results) == len(selected), "Missing or extra upgrade case results")
        by_index = {}
        for path in results:
            result = json.loads(path.read_text(encoding="utf-8"))
            gate.require(result["plan_sha256"] == gate.sha256(prepared / "plan.json"), "Stale or mixed upgrade results")
            index = result["index"]
            gate.require(type(index) is int and index in selected, "Unknown upgrade result index")
            gate.require(index not in by_index, "Duplicate upgrade result")
            gate.require(result["status"] == "passed", f"Upgrade case {index} failed")
            case_result = result["result"]
            gate.require(
                case_result["case"] == cases[index] and case_result["status"] == "passed", "Case identity mismatch"
            )
            acceptance = path.parent / "acceptance"
            backend = gate.validate_case_output(cases[index], acceptance)
            installed = json.loads((acceptance / "installed.json").read_text(encoding="utf-8"))
            gate.require(
                installed == case_result["installed"]
                and installed["source"]["powercontext"] == cases[index]["source_version"]
                and installed["target"]["powercontext"] == plan["target_version"],
                "Installed versions do not match the planned upgrade",
            )
            gate.require(backend == case_result["database"], "Backend evidence changed")
            by_index[index] = case_result
        evidence["cases"] = [by_index[index] for index in selected]
        if plan["release"]:
            gate.require(
                plan["mode"] == "supported-upgrades", "Update the reviewed support declaration before publication"
            )
        # Verify the originals once more before exposing a publication set.
        read_plan(root, prepared)
        evidence["complete"] = len(selected) == len(cases)
        if plan["selection"] == "seekdb-code":
            evidence["status"] = "selected-cases-passed"
            return []
        validated = output / "validated-dist"
        validated.mkdir()
        accepted = []
        for name, digest in plan["artifacts"].items():
            destination = validated / name
            shutil.copyfile(prepared / "inputs" / name, destination)
            gate.require(gate.sha256(destination) == digest, "Validated distribution copy changed")
            accepted.append(destination)
        write_json(
            output / "publication.json",
            {
                "artifacts": plan["artifacts"],
                "complete": evidence["complete"],
                "deferred_cases": evidence["deferred_cases"],
            },
        )
        evidence["status"] = "passed" if evidence["complete"] else "selected-cases-passed"
        return accepted
    finally:
        write_json(output / "result.json", evidence)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    preparation = commands.add_parser("prepare")
    preparation.add_argument("--dist", type=Path, default=Path("dist"))
    preparation.add_argument("--output", type=Path, required=True)
    preparation.add_argument("--release", action="store_true")
    preparation.add_argument("--selection", choices=SELECTIONS, default="full")
    preparation.add_argument("--release-event", type=Path)
    execution = commands.add_parser("case")
    execution.add_argument("--prepared", type=Path, required=True)
    execution.add_argument("--index", type=int, required=True)
    execution.add_argument("--output", type=Path, required=True)
    collection = commands.add_parser("collect")
    collection.add_argument("--prepared", type=Path, required=True)
    collection.add_argument("--reports", type=Path, required=True)
    collection.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            notes = None
            if args.release_event:
                notes = json.loads(args.release_event.read_text(encoding="utf-8"))["release"]["body"] or ""
            matrix = prepare(
                gate.ROOT,
                args.dist.resolve(),
                args.output.resolve(),
                release=args.release,
                release_notes=notes,
                selection=args.selection,
            )
            print(json.dumps(matrix))
            if github_output := os.environ.get("GITHUB_OUTPUT"):
                with Path(github_output).open("a", encoding="utf-8") as stream:
                    stream.write(f"matrix={json.dumps(matrix)}\n")
        elif args.command == "case":
            execute_case(gate.ROOT, args.prepared.resolve(), args.index, args.output.resolve())
        else:
            accepted = collect(gate.ROOT, args.prepared.resolve(), args.reports.resolve(), args.output.resolve())
            if accepted:
                print(f"Complete upgrade matrix passed; {len(accepted)} validated distributions")
            else:
                print("Selected upgrade cases passed; full acceptance remains incomplete; no publication artifacts")
    except (
        gate.GateError,
        OSError,
        ValueError,
        KeyError,
        subprocess.SubprocessError,
        tarfile.TarError,
        zipfile.BadZipFile,
        ElementTree.ParseError,
    ) as error:
        raise SystemExit(f"Parallel upgrade gate failed: {error}") from error  # noqa: TRY003 -- operator diagnostic


if __name__ == "__main__":
    main()
