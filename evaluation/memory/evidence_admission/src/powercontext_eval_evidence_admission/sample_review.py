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

"""Append content-free, partial human observations without rewriting frozen scores."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError

from .manifests import EvaluationError, file_hash, read_json
from .models import digest


class Selector(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    sample_id: str = Field(pattern=r"^[a-z0-9-]+$")
    dataset: Literal["synthetic-v1", "validation-v2", "validation-v3"]
    case: str = Field(pattern=r"^[a-z0-9-]+$")
    budget: Literal[2048, 4096, 8192]
    repetition: Literal[0, 1, 2]
    reported_finding: Literal[
        "no_problem_reported",
        "compound_candidate",
        "unicode_literal_loss",
        "attribution_varies",
        "attribution_failure_and_risk",
        "partial_output_reported",
    ]
    arm: Literal["complete-projection-v1", "window-reduction-v1", "bounded-fragments-v3"] | None = None
    input_index: int | None = Field(default=None, ge=0)


class Annotation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    sample_id: str = Field(pattern=r"^[a-z0-9-]+$")
    candidate_index: int = Field(ge=0)
    field: Literal["atomic", "false_attribution"]
    value: StrictBool
    reason_code: Literal["independent_assertions_combined", "tool_statement_attributed_to_current_user"]


class Declaration(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal["evidence-admission.human-sample-declaration.v1"]
    reviewer: str = Field(pattern=r"^[a-z0-9-]+$")
    recorded_on: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    review_date: str | None = None
    basis_result_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    basis_queue_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    preparation_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    state_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    execution_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    samples: list[Selector]
    annotations: list[Annotation]


def _require(condition, reason):
    if not condition:
        raise EvaluationError(reason)


def _hash_name(value):
    _require(
        isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value),
        "review_invalid_identity",
    )
    return value


def _matches(selector, record):
    return all(record[k] == getattr(selector, k) for k in ("dataset", "case", "budget", "repetition")) and all(
        getattr(selector, k) is None or record[k] == getattr(selector, k) for k in ("arm", "input_index")
    )


def _reference(record):
    keys = (
        "call_id",
        "row_id",
        "dataset",
        "case",
        "arm",
        "budget",
        "repetition",
        "input_index",
        "input_hash",
        "response_hash",
        "review_output_hash",
        "review_context_hash",
    )
    return {k: record[k] for k in keys}


def _validate_call(session, record, row, original, published):
    call_id, row_id = _hash_name(record["call_id"]), _hash_name(record["row_id"])
    keys = ("row_id", "input_index", "input_hash", "response_hash")
    _require(all(record[k] == original[k] == published[k] for k in keys), "review_record_binding_changed")
    _require(original["outcome"] == published["outcome"] == "returned", "review_rejected_output_not_accepted")
    _require(
        all(record[k] == row[k] for k in ("row_id", "dataset", "case", "arm", "budget", "repetition")),
        "review_row_binding_changed",
    )
    value = read_json(session / "inputs" / (row_id + ".json"))[record["input_index"]]
    _require(digest(value) == record["input_hash"], "review_input_changed")
    request = read_json(session / "audit" / (call_id + ".request.json"))
    _require(digest(request) == original["request_facts"]["http_request_hash"], "review_request_changed")
    _require(json.loads(request["messages"][2]["content"]) == value, "review_request_input_changed")
    raw = (session / "audit" / (call_id + ".response-bytes.json")).read_bytes()
    _require(digest(raw.hex()) == record["response_hash"], "review_response_changed")
    output = read_json(session / "audit" / (call_id + ".output.json"))
    _require(
        digest(output) == record["review_output_hash"] == original["output_hash"] == published["output_hash"],
        "review_output_changed",
    )
    receipt = read_json(session / "audit" / (call_id + ".review.json"))
    _require(
        receipt["context_hash"] == record["review_context_hash"] and receipt["output_hash"] == digest(output),
        "review_context_receipt_changed",
    )
    indices = set()
    for candidate in record["unresolved_candidates"]:
        index = candidate["candidate_index"]
        _require(index not in indices and 0 <= index < len(output["candidates"]), "review_candidate_index_changed")
        indices.add(index)
        data = output["candidates"][index]
        _require(
            digest(data) == candidate["candidate_hash"]
            and digest({"index": index, "candidate": data}) == candidate["candidate_key"],
            "review_candidate_binding_changed",
        )
    return output


def prepare_appendix(session, declaration_file, queue_file, result_file, fixture_root):
    """Validate recorded bytes, not the current native schema; partial labels never approve candidates."""
    declaration = Declaration.model_validate(read_json(declaration_file))
    _require(
        file_hash(result_file) == declaration.basis_result_sha256
        and file_hash(queue_file) == declaration.basis_queue_sha256,
        "review_basis_changed",
    )
    queue, result = read_json(queue_file), read_json(result_file)
    _require(queue["basis_result_sha256"] == declaration.basis_result_sha256, "review_queue_basis_changed")
    _require(result["freeze"]["revision"] == declaration.execution_revision, "review_execution_revision_changed")
    manifest, state = read_json(session / "preparation.json"), read_json(session / "state.json")
    _require(
        digest(manifest) == state["preparation_hash"] == declaration.preparation_hash
        and digest(state) == declaration.state_hash,
        "review_private_snapshot_changed",
    )
    _require(
        manifest["identity_hash"] == result["identity_hash"] and state["freeze"] == result["freeze"],
        "review_historical_identity_changed",
    )
    samples = {s.sample_id: s for s in declaration.samples}
    _require(len(samples) == len(declaration.samples), "review_duplicate_sample")
    original = {r["call_id"]: r for r in state["records"]}
    published = {r["call_id"]: r for r in result["records"]}
    _require(
        len(original) == len(state["records"]) and len(published) == len(result["records"]), "review_duplicate_call"
    )
    rows = {r["row_id"]: r for r in manifest["rows"]}
    eligible = [r for r in queue["records"] if r["review_priority"] == "accepted_output_semantics"]
    _require(len({r["call_id"] for r in eligible}) == len(eligible), "review_duplicate_queue_call")
    validated, coverage, resolved, observations = {}, [], {}, []
    for selector in declaration.samples:
        matches = [r for r in eligible if _matches(selector, r)]
        _require(bool(matches), "review_sample_not_in_queue")
        for record in matches:
            call_id = record["call_id"]
            if call_id not in validated:
                validated[call_id] = _validate_call(
                    session, record, rows[record["row_id"]], original[call_id], published[call_id]
                )
        coverage.append(
            {
                **selector.model_dump(),
                "resolution": "unique" if len(matches) == 1 else "ambiguous",
                "possible_records": [_reference(r) for r in matches],
                "whole_call_or_all_candidates_approved": False,
            }
        )
        if len(matches) != 1:
            continue
        record = matches[0]
        resolved[selector.sample_id] = record
        fixture = fixture_root / (selector.dataset + ".json")
        fixture_key = "evaluation/memory/evidence_admission/fixtures/" + fixture.name
        _require(file_hash(fixture) == manifest["identity"]["files"][fixture_key], "review_fixture_changed")
        case = next(c for c in read_json(fixture)["cases"] if c["case_id"] == selector.case)
        literals = [literal for claim in case["required"] for literal in claim.get("exact_literals", [])]
        if literals:
            texts = [c["text"] for c in validated[record["call_id"]]["candidates"]]
            observations.append(
                {
                    "sample_id": selector.sample_id,
                    **_reference(record),
                    "basis": "program_byte_check_not_semantic_judgement",
                    "literal_utf8_sha256": [hashlib.sha256(s.encode()).hexdigest() for s in literals],
                    "literal_present": [any(s.encode() in text.encode() for text in texts) for s in literals],
                }
            )
    annotations, seen = [], {}
    for annotation in declaration.annotations:
        _require(annotation.sample_id in resolved, "review_annotation_requires_unique_call")
        record = resolved[annotation.sample_id]
        candidate = next(
            (c for c in record["unresolved_candidates"] if c["candidate_index"] == annotation.candidate_index), None
        )
        if candidate is None:
            raise EvaluationError("review_annotation_not_in_unresolved_queue")
        key = (record["call_id"], candidate["candidate_key"], annotation.field)
        if key in seen:
            raise EvaluationError(
                "review_duplicate_annotation" if seen[key] == annotation.value else "review_conflicting_annotation"
            )
        seen[key] = annotation.value
        annotations.append(
            {
                **annotation.model_dump(),
                **_reference(record),
                **candidate,
                "reviewer": declaration.reviewer,
                "reviewer_kind": "human",
                "basis": "user_reported_judgement_with_program_verified_binding",
                "complete_v10_semantic_review": False,
            }
        )
    budget_rows = []
    for row in manifest["rows"]:
        if row["case"] not in {"many-source-overflow", "validation-reordered-sources"}:
            continue
        if not any(_matches(s, {**row, "input_index": 0}) for s in declaration.samples):
            continue
        records = [r for r in state["records"] if r["row_id"] == row["row_id"]]
        budget_rows.append(
            {
                **{k: row[k] for k in ("row_id", "dataset", "case", "arm", "budget", "repetition")},
                "input_count": len(row["sample"]["input_hashes"]),
                "required_atom_count": len(row["required_atom_hashes"]),
                "admitted_atom_count": len(row["admitted_atom_hashes"]),
                "request_outcomes": dict(Counter(r["outcome"] for r in records)),
                "generation_retention_established": False,
            }
        )
    mixed = [r for r in resolved.values() if r["case"] == "mixed-scaffolding" and r["budget"] == 2048]
    repeat = {
        "case": "mixed-scaffolding",
        "budget": 2048,
        "call_ids": [r["call_id"] for r in mixed],
        "distinct_input_hashes": len({r["input_hash"] for r in mixed}),
        "distinct_request_hashes": len({original[r["call_id"]]["request_facts"]["http_request_hash"] for r in mixed}),
        "distinct_output_hashes": len({r["review_output_hash"] for r in mixed}),
        "is_new_offline_replay": False,
    }
    summary = {
        "schema": "evidence-admission.human-sample-summary.v1",
        "decision": result["decision"],
        "basis_result_sha256": declaration.basis_result_sha256,
        "execution_revision": declaration.execution_revision,
        "reported_sample_entries": len(coverage),
        "reported_scenarios": len({s.case for s in declaration.samples}),
        "uniquely_bound_entries": len(resolved),
        "ambiguous_entries": len(coverage) - len(resolved),
        "verified_possible_calls": len(validated),
        "human_partial_candidate_judgements": len(annotations),
        "human_false_attribution_true": sum(a["field"] == "false_attribution" and a["value"] for a in annotations),
        "human_atomic_false": sum(a["field"] == "atomic" and not a["value"] for a in annotations),
        "fully_adjudicated_candidates_added": 0,
        "original_semantic_unresolved_candidates": result["semantic_unresolved_candidates"],
        "original_scores_changed": False,
        "production_authorized": False,
        "provider_requests": 0,
        "current_native_pipeline_or_schema_revalidated": False,
        "independent_second_reviewer_established": False,
    }
    return {
        "declaration.json": declaration.model_dump(),
        "coverage.json": {"records": coverage},
        "human-judgements.json": {"records": annotations},
        "observations.json": {
            "literal_checks": observations,
            "multi_request_rows": budget_rows,
            "repeated_request_comparison": repeat,
        },
        "summary.json": summary,
    }


def write_appendix(output, session, documents):
    _require(
        not output.resolve().is_relative_to(session.resolve())
        and not session.resolve().is_relative_to(output.resolve()),
        "review_output_overlaps_session",
    )
    _require(not output.exists() and not output.is_symlink(), "review_output_already_exists")
    output.mkdir(mode=0o700)
    summary = documents["summary.json"]
    documents = {
        **documents,
        "README.zh.md": (
            "# 人工抽查附录\n\n"
            f"用户报告 {summary['reported_sample_entries']} 条抽查描述，覆盖 {summary['reported_scenarios']} 个场景。"
            f"其中 {summary['uniquely_bound_entries']} 条在声明的优先队列内可唯一定位，"
            f"{summary['ambiguous_entries']} 条仍需补充 arm 或请求下标。\n\n"
            f"已绑定人工判断：错误归因 {summary['human_false_attribution_true']} 项，"
            f"非原子候选 {summary['human_atomic_false']} 项。这些是部分维度判断，不是完整逐候选语义评分。\n\n"
            f"原机器未决 {summary['original_semantic_unresolved_candidates']} 项保持不变；"
            "没有修改原响应、评分、事实标注或冻结门槛。决策仍为 insufficient_evidence。\n\n"
            "coverage.json 中的多选记录不作人工签认。observations.json 的字节检查与多请求计数"
            "属于程序核验，不代替语义判断。完整样本须聚合全部请求，不能以单次输出认定只准入部分事实。\n\n"
            "本附录只校验已记录 JSON 字节与原绑定，不宣称当前运行时重新执行了历史 Pipeline。"
            "原冻结环境的额外验证和工程检查另见交付说明。公开文件不含原始输入或模型响应。\n"
        ),
    }
    files = {}
    for name, value in documents.items():
        raw = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2) + "\n"
        path = output / name
        with path.open("x", encoding="utf-8") as stream:
            stream.write(raw)
        files[name] = {"sha256": file_hash(path), "bytes": path.stat().st_size}
    index = {
        "schema": "evidence-admission.human-sample-index.v1",
        "files": files,
        "tool_sha256": file_hash(Path(__file__)),
        "self_excluded": True,
    }
    (output / "artifact-index.json").write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n")
    (output / "COMPLETE.json").write_text(
        json.dumps(
            {
                "index_sha256": file_hash(output / "artifact-index.json"),
                "complete_artifact_not_complete_evaluation": True,
            },
            indent=2,
        )
        + "\n"
    )


def verify_appendix(directory):
    marker = read_json(directory / "COMPLETE.json")
    _require(marker["index_sha256"] == file_hash(directory / "artifact-index.json"), "review_index_changed")
    index = read_json(directory / "artifact-index.json")
    for name, expected in index["files"].items():
        _require(Path(name).name == name and name not in {".", ".."}, "review_invalid_artifact_path")
        path = directory / name
        _require(
            not path.is_symlink()
            and file_hash(path) == expected["sha256"]
            and path.stat().st_size == expected["bytes"],
            "review_artifact_changed",
        )
    return {"verified": True, "files": len(index["files"])}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build")
    for name in ("session", "declaration", "queue", "result", "fixtures", "output"):
        build.add_argument("--" + name, type=Path, required=True)
    commands.add_parser("verify").add_argument("directory", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "verify":
            result = verify_appendix(args.directory)
        else:
            documents = prepare_appendix(args.session, args.declaration, args.queue, args.result, args.fixtures)
            write_appendix(args.output, args.session, documents)
            result = documents["summary.json"]
    except (EvaluationError, ValidationError, OSError, ValueError, KeyError, IndexError) as error:
        reason = str(error) if isinstance(error, EvaluationError) else "review_invalid_input_or_output"
        print(json.dumps({"error": reason}))
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
