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

"""Controlled-runner admission. Hashes bind approvals; the host supplies authority.

The approval anchor is an explicit API/CLI input, never taken from a plan.
Only the trusted evaluator may provision it after checking external evidence.
This module cannot establish the truth of DBA grants or security attestations.
"""

from __future__ import annotations

# ruff: noqa: TRY003 - bounded diagnostics exclude request values and secrets.
import hashlib
import json
import os
import re
import stat
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from powercontext_datus import HOLDOUT_SHA256, HOLDOUT_SIZE
from powercontext_datus.freeze import IntegrityError, digest_json, snapshot
from powercontext_datus.transport import tls_context, trust_bytes

ISSUE_ID = "01a068bf-a292-7d75-9c20-4018e69a6e3f"
LIVE_KINDS = {"independent_learning", "independent_development", "independent_formal", "native_smoke"}
CONDITIONS = {
    "verified_transport",
    "read_only_grants",
    "immutable_data_version",
    "protected_runner",
    "rotation_resolved",
    "model_authorized",
}


def read_document(path: Path) -> tuple[bytes, Any]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 8 * 1024 * 1024:
        raise IntegrityError("bounded regular admission document required")
    data = path.read_bytes()
    try:
        return data, json.loads(data)
    except (ValueError, RecursionError):
        # Includes JSON syntax, encoding, integer-size and nesting failures.
        # Normalize at the parser so post-worker checks cannot lose its result;
        # never expose decoder payloads through a chained exception.
        raise IntegrityError("invalid JSON evidence document") from None


def receipt(ref: Any) -> tuple[str, Any]:
    if not isinstance(ref, dict) or set(ref) != {"file", "sha256"}:
        raise IntegrityError("exact receipt file/digest required")
    data, body = read_document(Path(ref["file"]))
    digest = hashlib.sha256(data).hexdigest()
    if digest != ref["sha256"]:
        raise IntegrityError("admission receipt changed")
    return digest, body


def read_secret(path: str) -> str:
    ref = Path(path)
    info = ref.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise IntegrityError("secret reference must be an owned regular file with no group/other access")
    value = ref.read_text().strip()
    if not value:
        raise IntegrityError("empty task-authorized secret reference")
    return value


def profile_identity(plan: dict[str, Any]) -> str:
    return digest_json({
        "public": plan["public"],
        "timeout_seconds": plan["timeout_seconds"],
        "common": hashlib.sha256(Path(plan["common_file"]).read_bytes()).hexdigest()
        if plan.get("common_file")
        else None,
        "skills": {arm: snapshot(Path(value["skill_root"])) for arm, value in plan.get("arms", {}).items()},
        "runtime_profile_sha256": plan.get("runtime_profile_sha256"),
    })


def evaluation_identity(plan: dict[str, Any]) -> str:
    _, admission = read_document(Path(plan["admission_file"]))
    return digest_json({
        "profile": profile_identity(plan),
        "phase": plan["evidence_kind"],
        "tasks": plan.get("tasks", []),
        "oracle": hashlib.sha256(Path(plan["oracle_file"]).read_bytes()).hexdigest()
        if plan.get("oracle_file")
        else None,
        "admission": {key: value for key, value in admission.items() if key != "safety_approval"},
        "secret_refs": plan.get("secret_refs"),
    })


def validate_safety(plan: dict[str, Any], anchor: str | None) -> dict[str, Any]:  # noqa: C901 - explicit ordered admission checks
    if plan["evidence_kind"] not in LIVE_KINDS:
        raise IntegrityError("unknown live phase")
    if not isinstance(anchor, str) or not re.fullmatch(r"[0-9a-f]{64}", anchor):
        raise IntegrityError("controlled runner must supply an out-of-plan approval digest")
    _, admission = read_document(Path(plan["admission_file"]))
    ref = admission.get("safety_approval")
    if not isinstance(ref, dict) or ref.get("sha256") != anchor:
        raise IntegrityError("runner approval does not match the plan")
    _, approval = receipt(ref)
    required = {
        "version",
        "issue_id",
        "phase",
        "grant_id",
        "runner_id",
        "approved_by",
        "subject",
        "issued_at",
        "expires_at",
        "evaluation_sha256",
        "conditions",
        "receipts",
    }
    if plan["evidence_kind"] == "independent_formal":
        required.add("single_run_ledger")
    if not isinstance(approval, dict) or set(approval) != required:
        raise IntegrityError("invalid controlled-runner approval schema")
    if (
        type(approval["version"]) is not int
        or approval["version"] != 1
        or approval["issue_id"] != ISSUE_ID
        or approval["phase"] != plan["evidence_kind"]
    ):
        raise IntegrityError("approval scope/phase mismatch")
    if any(
        not isinstance(approval[key], str) or not approval[key].strip()
        for key in ("grant_id", "runner_id", "approved_by", "subject")
    ):
        raise IntegrityError("approved runner and accountable identities required")
    issued, expires = approval["issued_at"], approval["expires_at"]
    if (
        type(issued) is not int
        or type(expires) is not int
        or not issued <= time.time() < expires
        or not 0 < expires - issued <= 86400
    ):
        raise IntegrityError("approval expired or invalid validity interval")
    if approval["evaluation_sha256"] != evaluation_identity(plan):
        raise IntegrityError("approval does not cover these exact evaluation inputs")
    if plan["evidence_kind"] != "native_smoke" and not re.fullmatch(
        r"[0-9a-f]{64}", plan.get("runtime_profile_sha256", "")
    ):
        raise IntegrityError("approved execution/runtime profile required")
    if (
        not isinstance(approval["conditions"], dict)
        or set(approval["conditions"]) != CONDITIONS
        or any(value is not True for value in approval["conditions"].values())
    ):
        raise IntegrityError("external safety prerequisites are not all verified")
    if not isinstance(approval["receipts"], dict) or set(approval["receipts"]) != CONDITIONS:
        raise IntegrityError("external safety evidence references required")
    for ref in approval["receipts"].values():
        receipt(ref)
    db, model = plan["public"]["database"], plan["public"]["model"]
    if (
        set(db) != {"type", "name", "host", "port", "username", "tls"}
        or db["type"] != "mysql"
        or any(not isinstance(db[k], str) or not db[k].strip() for k in ("name", "host", "username"))
        or type(db["port"]) is not int
        or not 1 <= db["port"] <= 65535
    ):
        raise IntegrityError("approved explicit MySQL identity required")
    endpoint = urlsplit(model["base_url"])
    if (
        model.get("type") != "openai"
        or not isinstance(model.get("model"), str)
        or not model["model"].strip()
        or endpoint.scheme != "https"
        or not endpoint.hostname
        or endpoint.username
        or endpoint.password
        or endpoint.query
        or endpoint.fragment
    ):
        raise IntegrityError("approved explicit HTTPS model identity required")
    for name in ("database", "model"):
        tls_context(plan["public"][name]["tls"])
    return approval


def evidence_identity(plan: dict[str, Any]) -> dict[str, Any]:
    """Freeze transitive receipt bytes, not merely the JSON containing their paths."""
    _, admission = read_document(Path(plan["admission_file"]))
    found: dict[str, str] = {}

    def visit(value, depth=0):
        if depth > 12 or len(found) > 128:
            raise IntegrityError("admission reference budget exceeded")
        if isinstance(value, dict):
            if set(value) == {"file", "sha256"}:
                digest, body = receipt(value)
                path = value["file"]
                if path not in found:
                    found[path] = digest
                    visit(body, depth + 1)
            else:
                for child in value.values():
                    visit(child, depth + 1)
        elif isinstance(value, list):
            for child in value:
                visit(child, depth + 1)

    visit(admission)
    return {
        "receipts": found,
        "ca": {
            name: hashlib.sha256(trust_bytes(plan["public"][name]["tls"]).encode()).hexdigest()
            for name in ("model", "database")
        },
    }


def question_fingerprint(question: str) -> str:
    return digest_json(" ".join(question.split()).casefold())


def validate_formal(plan: dict[str, Any], admission: dict[str, Any]) -> None:  # noqa: C901 - one formal acceptance boundary
    tasks = plan["tasks"]
    if len(tasks) != HOLDOUT_SIZE or admission.get("holdout_source_sha256") != HOLDOUT_SHA256:
        raise IntegrityError("formal run requires the registered complete 46-question holdout")
    _, registration = receipt(admission.get("formal_roster_receipt"))
    if registration != {"source_sha256": HOLDOUT_SHA256, "roster_sha256": digest_json(tasks)}:
        raise IntegrityError("formal questions differ from the independently registered roster")
    _, learning = receipt(admission.get("learning_roster"))
    _, development = receipt(admission.get("development_roster"))
    seen_ids: set[str] = set()
    seen_questions: set[str] = set()
    for roster in (
        learning,
        development,
        [{"task_id": t["task_id"], "question_sha256": question_fingerprint(t["question"])} for t in tasks],
    ):
        if not isinstance(roster, list) or not roster:
            raise IntegrityError("independent learning/development rosters required")
        for item in roster:
            if (
                not isinstance(item, dict)
                or set(item) != {"task_id", "question_sha256"}
                or not isinstance(item["task_id"], str)
                or not item["task_id"]
                or not isinstance(item["question_sha256"], str)
                or not re.fullmatch(r"[0-9a-f]{64}", item["question_sha256"])
            ):
                raise IntegrityError("invalid independent roster identity")
            if item["task_id"] in seen_ids or item["question_sha256"] in seen_questions:
                raise IntegrityError("learning/development/holdout overlap")
            seen_ids.add(item["task_id"])
            seen_questions.add(item["question_sha256"])
    _, report = receipt(admission.get("development_report"))
    if (
        report.get("evidence_kind") != "independent_development"
        or report.get("state_valid") is not True
        or report.get("profile_sha256") != profile_identity(plan)
    ):
        raise IntegrityError("independently reviewed development evidence differs from formal profile")
    if report.get("real_development_runs") != {arm: len(development) for arm in ("native", "enhanced")}:
        raise IntegrityError("both development arms must have complete real-run evidence")
    expected = {(arm, item["task_id"]) for arm in ("native", "enhanced") for item in development}
    cases = report.get("cases", [])
    if (
        len(cases) != len(expected)
        or {(case["arm"], case["task_id"]) for case in cases} != expected
        or any(
            case.get("verdict", {}).get("trace_complete") is not True
            or case.get("verdict", {}).get("native_execution") is not True
            for case in cases
        )
    ):
        raise IntegrityError("complete development coverage must precede formal freeze")


def claim_formal(approval: dict[str, Any], manifest_digest: str, plan_digest: str, run_id: str) -> None:
    path = Path(approval["single_run_ledger"])
    parent = path.parent.lstat()
    if (
        not path.is_absolute()
        or not stat.S_ISDIR(parent.st_mode)
        or parent.st_uid != os.getuid()
        or parent.st_mode & 0o077
    ):
        raise IntegrityError("formal single-run ledger requires a private owned parent")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(
            {
                "grant_id": approval["grant_id"],
                "manifest_sha256": manifest_digest,
                "plan_sha256": plan_digest,
                "run_id": run_id,
                "state": "started",
            },
            stream,
        )
        stream.flush()
        os.fsync(stream.fileno())
    fsync_parent(path)


def claim_dispatch(approval, plan, arm, task, run_id, manifest_digest):
    path = Path(approval["single_run_ledger"])
    _, ledger = read_document(path)
    if ledger != {
        "grant_id": approval["grant_id"],
        "manifest_sha256": manifest_digest,
        "plan_sha256": digest_json(plan),
        "run_id": run_id,
        "state": "started",
    }:
        raise IntegrityError("formal dispatch differs from its single-run claim")
    dispatch = path.with_name(path.name + "." + digest_json({"arm": arm, "task": task}))
    fd = os.open(dispatch, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump({"run_id": run_id, "state": "claimed_before_dispatch"}, stream)
        stream.flush()
        os.fsync(stream.fileno())
    fsync_parent(dispatch)


def fsync_parent(path: Path) -> None:
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def dispatch_permit(plan: dict[str, Any], approval: dict[str, Any], task: dict[str, str]) -> dict[str, Any]:
    return {
        "phase": plan["evidence_kind"],
        "public_sha256": digest_json(plan["public"]),
        "task_sha256": digest_json(task),
        "expires_at": approval["expires_at"],
        "grant_id": approval["grant_id"],
    }


def validate_dispatch(request: dict[str, Any]) -> None:
    permit = request.get("admission_permit", {})
    if (
        set(permit) != {"phase", "public_sha256", "task_sha256", "expires_at", "grant_id"}
        or permit["phase"] != request["evidence_kind"]
        or permit["public_sha256"] != digest_json(request["public"])
        or permit["task_sha256"] != digest_json(request["task"])
        or type(permit["expires_at"]) is not int
        or permit["expires_at"] <= time.time()
        or not permit["grant_id"]
    ):
        raise IntegrityError("worker requires a current supervisor-validated dispatch permit")
