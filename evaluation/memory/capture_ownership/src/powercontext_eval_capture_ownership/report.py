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

"""Content-free metrics backed by explicit, output-bound human support labels."""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, cast

from .models import ARMS, Manifest, ReviewFile, RunConfig, contains_literal, fingerprint, read_json, write_json


def review_template(run: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "capture-ownership.review.v1",
        "run_sha256": fingerprint(run),
        "cases": [
            {
                "case_id": row["case_id"],
                "arm": row["arm"],
                "repetition": row["repetition"],
                "output_sha256": row["output_sha256"],
                "reviewer": None,
                "complete": False,
                "candidates": [
                    {
                        "candidate_index": index,
                        "candidate_sha256": fingerprint(candidate),
                        "claim_ids": None,
                        "support_span_ids": [],
                        "support_complete": None,
                        "unsupported_claim": None,
                        "current_user_attribution": None,
                        "duplicate_group": None,
                    }
                    for index, candidate in enumerate(row["candidates"])
                ],
            }
            for row in run["rows"]
        ],
    }


def _row_key(row: Any) -> tuple[str, str, int]:
    if isinstance(row, dict):
        return row["case_id"], row["arm"], row["repetition"]
    return row.case_id, row.arm, row.repetition


def _validate_export(row: dict[str, Any]) -> None:
    """Refuse content in fields that the aggregate exporter is allowed to copy."""

    def number(value: Any, *, integer: bool = False) -> bool:
        return (
            not isinstance(value, bool)
            and isinstance(value, int if integer else (int, float))
            and math.isfinite(value)
            and value >= 0
        )

    for field in ("source_sha256", "materialization_sha256", "input_sha256", "output_sha256"):
        if not isinstance(row[field], str) or not re.fullmatch(r"[0-9a-f]{64}", row[field]):
            raise ValueError("aggregate hash field is not a digest")
    if row["status"] not in {"completed", "skipped", "budget_rejected", "generation_failed"}:
        raise ValueError("unknown aggregate outcome")
    if row["turn_status"] not in {"eligible", "empty", "unknown"} or row["turn_reason"] not in {
        "validated_host_envelope",
        "missing_host_envelope",
        "malformed_user_wrapper",
        "malformed_host_block",
        "unfinished_host_record",
        "unrecognized_host_record",
        "missing_current_wrapper",
        "ambiguous_host_parser",
    }:
        raise ValueError("unknown aggregate parser outcome")
    if not isinstance(row["parser_matches_ground_truth"], bool):
        raise TypeError("invalid parser comparison")
    if any(
        candidate["kind"] not in {"fact", "preference", "decision", "constraint", "working_note"}
        for candidate in row["candidates"]
    ):
        raise ValueError("unknown candidate kind")
    occupancy = row["occupancy"]
    if set(occupancy) != {"labeled_utf8_bytes", "text_carriers", "serialized_utf8_bytes"}:
        raise ValueError("unrecognized occupancy field")
    owners = occupancy["labeled_utf8_bytes"]
    if not set(owners) <= {"current", "history", "prepared_context", "scaffold", "skill", "quoted", "summary"}:
        raise ValueError("unknown ownership category")
    values = [
        *owners.values(),
        occupancy["text_carriers"],
        occupancy["serialized_utf8_bytes"],
        row["estimated_input_tokens"],
    ]
    if not all(number(value, integer=True) for value in values) or not number(row["generation_seconds"]):
        raise ValueError("invalid aggregate measurement")
    if not number(row.get("cost_reservation_cny", 0)):
        raise ValueError("invalid cost reservation")
    usage = row["provider_usage"]
    if usage is not None and (
        set(usage) != {"requests", "input_tokens", "output_tokens"}
        or any(value is not None and not number(value, integer=True) for value in usage.values())
    ):
        raise ValueError("invalid provider usage")


def summarize(
    manifest: Manifest,
    lock: dict[str, Any],
    run: dict[str, Any],
    reviews: ReviewFile | None = None,
) -> dict[str, Any]:
    """Never infer semantic support from a matching literal or a Source citation alone."""

    config = RunConfig.model_validate(lock["config"])
    if (
        not re.fullmatch(r"[0-9a-f]{40}", lock["code_sha"])
        or not re.fullmatch(
            r"powercontext\.atomic-memory\.extract\.(coding|conversation)\.v[0-9]+", lock["prompt_version"]
        )
        or not re.fullmatch(r"[0-9a-f]{64}", lock["prompt_sha256"])
    ):
        raise ValueError("invalid frozen public provenance")
    if not run.get("complete") or run["lock_sha256"] != fingerprint(lock):
        raise ValueError("run is incomplete or does not match the frozen experiment")
    if fingerprint(manifest.model_dump(mode="json")) != lock["manifest_sha256"]:
        raise ValueError("run manifest has changed")
    cases = {case.case_id: case for case in manifest.cases}
    expected = {
        (case.case_id, arm, repetition)
        for case in manifest.cases
        for repetition in range(config.repetitions)
        for arm in ARMS
    }
    rows = {_row_key(row): row for row in run["rows"]}
    if set(rows) != expected or len(rows) != len(run["rows"]):
        raise ValueError("run has missing, duplicate, or unknown arm/case/repetition rows")
    for row in rows.values():
        _validate_export(row)
        if row["output_sha256"] != fingerprint(row["candidates"]):
            raise ValueError("candidate output hash does not match the saved output")
        if row["input_sha256"] != fingerprint(row["input"]):
            raise ValueError("prepared input hash does not match the saved input")
        for candidate in row["candidates"]:
            if not candidate["evidence_ids"] or not set(candidate["evidence_ids"]) <= {row["evidence_id"]}:
                raise ValueError("candidate cites unavailable Source evidence")
    reviewed = {} if reviews is None else {_row_key(review): review for review in reviews.cases}
    if reviews is not None and (
        reviews.run_sha256 != fingerprint(run) or set(reviewed) != expected or len(reviewed) != len(reviews.cases)
    ):
        raise ValueError("review must match the exact run and every arm/case/repetition")

    output_rows: list[dict[str, Any]] = []
    for key, row in rows.items():
        case = cases[row["case_id"]]
        claim_by_id = {claim.claim_id: claim for claim in case.claims}
        span_owners = {span.span_id: span.owner for span in case.spans}
        required = {claim.claim_id for claim in case.claims if claim.required_current}
        review = reviewed.get(key)
        supported_current: set[str] = set()
        critical = 0
        ownership_contamination = 0
        unsupported = 0
        false_attribution = 0
        exact_literal_failures = 0
        support_categories: Counter[str] = Counter()
        duplicate_groups: Counter[str] = Counter()
        accepted = review is not None and review.complete and bool(review.reviewer and review.reviewer.strip())
        if review is not None:
            if review.output_sha256 != row["output_sha256"] or len(review.candidates) != len(row["candidates"]):
                raise ValueError("review candidate inventory does not match the output")
            annotations = {item.candidate_index: item for item in review.candidates}
            if set(annotations) != set(range(len(row["candidates"]))) or len(annotations) != len(review.candidates):
                raise ValueError("review has duplicate or unmatched candidate indices")
            for index, candidate in enumerate(row["candidates"]):
                label = annotations[index]
                if label.candidate_sha256 != fingerprint(candidate):
                    raise ValueError("review candidate hash does not match")
                values = (
                    label.claim_ids,
                    label.support_complete,
                    label.unsupported_claim,
                    label.current_user_attribution,
                )
                if any(value is None for value in values):
                    accepted = False
                    continue
                claim_ids = set(label.claim_ids or ())
                if not claim_ids <= claim_by_id.keys():
                    raise ValueError("review references a claim absent from frozen ground truth")
                if not set(label.support_span_ids) <= span_owners.keys():
                    raise ValueError("review references an ownership span absent from frozen ground truth")
                if not claim_ids and not label.support_span_ids and not label.unsupported_claim:
                    raise ValueError("candidate requires labeled support or an explicit unsupported claim label")
                if not label.support_complete:
                    accepted = False
                support_spans = set(label.support_span_ids) | {
                    span for claim_id in claim_ids for span in claim_by_id[claim_id].span_ids
                }
                owners = {span_owners[span] for span in support_spans}
                category = next(iter(owners)) if len(owners) == 1 else "mixed" if owners else "unsupported"
                support_categories[category] += 1
                outside_current = bool(owners) and "current" not in owners
                attributed = bool(label.current_user_attribution and owners - {"current"})
                false_attribution += attributed
                unsupported += bool(label.unsupported_claim)
                ownership_contamination += bool(outside_current or attributed)
                critical += bool(outside_current or attributed or label.unsupported_claim)
                for claim_id in claim_ids & required:
                    literals = claim_by_id[claim_id].exact_literals
                    if any(not contains_literal(candidate["text"], literal) for literal in literals):
                        exact_literal_failures += 1
                    elif not label.unsupported_claim and label.support_complete:
                        supported_current.add(claim_id)
                if label.duplicate_group is not None:
                    duplicate_groups[label.duplicate_group] += 1
        if not accepted:
            # Pending labels are not evidence of clean or retained generation.
            critical_count: int | None = None
            retained: int | None = None
        else:
            critical_count = critical
            retained = len(supported_current)
        output_rows.append(
            {
                "case_id": row["case_id"],
                "arm": row["arm"],
                "repetition": row["repetition"],
                "status": row["status"],
                "turn_status": row["turn_status"],
                "turn_reason": row["turn_reason"],
                "parser_matches_ground_truth": row["parser_matches_ground_truth"],
                "source_sha256": row["source_sha256"],
                "materialization_sha256": row["materialization_sha256"],
                "input_sha256": row["input_sha256"],
                "output_sha256": row["output_sha256"],
                "occupancy": row["occupancy"],
                "estimated_input_tokens": row["estimated_input_tokens"],
                "candidate_count": len(row["candidates"]),
                "candidate_kinds": dict(Counter(item["kind"] for item in row["candidates"])),
                "review_complete": accepted,
                "support_categories": dict(support_categories) if accepted else {},
                "critical_candidates": critical_count,
                "ownership_contamination_candidates": ownership_contamination if accepted else None,
                "unsupported_candidates": unsupported if accepted else None,
                "false_current_attribution": false_attribution if accepted else None,
                "near_duplicate_candidates": sum(count - 1 for count in duplicate_groups.values())
                if accepted
                else None,
                "required_current_claims": len(required),
                "retained_current_claims": retained,
                "required_current_retention": (retained / len(required)) if retained is not None and required else None,
                "exact_literal_failures": exact_literal_failures if accepted else None,
                "generation_seconds": row["generation_seconds"],
                "provider_usage": row["provider_usage"],
                "cost_reservation_cny": row.get("cost_reservation_cny", 0),
            }
        )
    # Same Source/evidence identity, prompt, config, ordering and budgets in both arms.
    for case in manifest.cases:
        case_rows = [row for row in run["rows"] if row["case_id"] == case.case_id]
        for field in ("source_sha256", "materialization_sha256", "source_position", "evidence_id"):
            if len({row[field] for row in case_rows}) != 1:
                raise ValueError("paired arms do not use the same exact Source and evidence identity")
        for arm in ARMS:
            if len({row["input_sha256"] for row in case_rows if row["arm"] == arm}) != 1:
                raise ValueError("prepared inputs are not deterministic across repetitions")

    qualified: defaultdict[str, int] = defaultdict(int)
    by_key = {_row_key(row): row for row in output_rows}
    valid_statuses = {"completed", "skipped"}
    all_ready = all(
        row["status"] in valid_statuses and row["review_complete"] and row["parser_matches_ground_truth"]
        for row in output_rows
    )
    if run["mode"] == "live" and all_ready:
        for row in output_rows:
            if row["arm"] != ARMS[0] or not row["ownership_contamination_candidates"]:
                continue
            other = by_key[(row["case_id"], ARMS[1], row["repetition"])]
            if (
                other["status"] == "completed"
                and other["required_current_claims"] > 0
                and other["retained_current_claims"] == other["required_current_claims"]
                and other["critical_candidates"] == 0
                and other["exact_literal_failures"] == 0
            ):
                qualified[row["case_id"]] += 1
    if any(count >= lock["decision_policy"]["critical_repetitions"] for count in qualified.values()):
        decision = "projection_follow_up_required"
    elif (
        run["mode"] == "live"
        and all_ready
        and any(row["required_current_claims"] for row in output_rows)
        and all(row["critical_candidates"] == 0 for row in output_rows)
        and all(row["exact_literal_failures"] == 0 for row in output_rows if row["arm"] == ARMS[0])
        and all(
            row["retained_current_claims"] == row["required_current_claims"]
            for row in output_rows
            if row["required_current_claims"] and row["arm"] == ARMS[0]
        )
    ):
        decision = "no_production_change"
    else:
        decision = "insufficient_evidence"
    totals: dict[str, Any] = {}
    for arm in ARMS:
        arm_rows = [row for row in output_rows if row["arm"] == arm]
        labels_complete = all(row["review_complete"] for row in arm_rows)
        candidates = sum(row["candidate_count"] for row in arm_rows)
        required = sum(row["required_current_claims"] for row in arm_rows)
        generated = [row for row in arm_rows if row["status"] in {"completed", "generation_failed"}]
        usage_complete = all(
            row["provider_usage"] is not None
            and all(row["provider_usage"][key] is not None for key in ("input_tokens", "output_tokens"))
            for row in generated
        ) and bool(generated)
        input_tokens = sum(row["provider_usage"]["input_tokens"] for row in generated) if usage_complete else None
        output_tokens = sum(row["provider_usage"]["output_tokens"] for row in generated) if usage_complete else None
        known_usage = [row["provider_usage"] for row in generated if row["provider_usage"] is not None]
        known_inputs = [usage["input_tokens"] for usage in known_usage if usage["input_tokens"] is not None]
        known_outputs = [usage["output_tokens"] for usage in known_usage if usage["output_tokens"] is not None]
        known_input_tokens = sum(known_inputs) if known_inputs else None
        known_output_tokens = sum(known_outputs) if known_outputs else None
        critical = sum(row["critical_candidates"] for row in arm_rows) if labels_complete else None
        retained = sum(row["retained_current_claims"] for row in arm_rows) if labels_complete else None
        totals[arm] = {
            "rows": len(arm_rows),
            "outcomes": dict(Counter(row["status"] for row in arm_rows)),
            "candidate_count": candidates,
            "critical_candidates": critical,
            "ownership_contamination_candidates": sum(row["ownership_contamination_candidates"] for row in arm_rows)
            if labels_complete
            else None,
            "critical_candidate_rate": critical / candidates if critical is not None and candidates else None,
            "required_current_claims": required,
            "retained_current_claims": retained,
            "required_current_retention": retained / required if retained is not None and required else None,
            "provider_input_tokens": input_tokens,
            "provider_output_tokens": output_tokens,
            "known_provider_input_tokens": known_input_tokens,
            "known_provider_output_tokens": known_output_tokens,
            "provider_usage_complete": usage_complete,
            "known_usage_cost_cny": (
                (known_input_tokens or 0) * config.input_cny_per_million
                + (known_output_tokens or 0) * config.output_cny_per_million
            )
            / 1_000_000
            if known_input_tokens is not None or known_output_tokens is not None
            else None,
            "cost_reservation_cny": sum(row["cost_reservation_cny"] for row in arm_rows),
            "generation_seconds": sum(row["generation_seconds"] for row in arm_rows),
        }
    return {
        "schema_version": "capture-ownership.report.v1",
        "decision": decision,
        "generation_mode": run["mode"],
        "corpus": manifest.corpus,
        "run_sha256": fingerprint(run),
        "lock_sha256": fingerprint(lock),
        "manifest_sha256": lock["manifest_sha256"],
        "code_sha": lock["code_sha"],
        "prompt_version": lock["prompt_version"],
        "prompt_sha256": lock["prompt_sha256"],
        "config_sha256": fingerprint(lock["config"]),
        "harness_sha256": fingerprint(lock["file_sha256"]),
        "analysis_sha256": fingerprint(
            {name: (Path(__file__).parent / name).read_text(encoding="utf-8") for name in ("report.py", "models.py")}
        ),
        "totals_by_arm": totals,
        "reproduced_case_ids": sorted(
            key for key, count in qualified.items() if count >= lock["decision_policy"]["critical_repetitions"]
        ),
        "rows": output_rows,
        "limitations": [
            "Synthetic host replay does not qualify a native WorkBuddy installation.",
            "Eligible means syntactically identified under the frozen host envelope hypothesis, not trusted authority.",
            "Only the configured extraction profile/provider and committed cases were evaluated.",
            "Input occupancy and empty offline outputs do not establish candidate contamination or model use.",
        ],
    }


FOLLOW_UP = """# Follow-up: versioned host-owned current-turn evidence projection

Reproduced cases and pinned inputs: see the accompanying report.json and its run/lock hashes.
This is an implementation-ready issue draft; no production registration or API change is made by the evaluation.

- Preserve the exact raw submission and immutable Source/materialization identity.
- Bind the derived text-evidence view to Source identity, materialization digest, parser version and projection version.
- Keep payload-bearing metadata out of the bounded extraction view; a content-only replacement is insufficient.
- The owning host parser creates the view. Unknown, forged, nested or ambiguous boundaries remain ineligible.
- Ownership is not evidence authority; retain Source declaration/verification and neutral lifecycle semantics.
- Duplicate delivery is idempotent; conflicting views for the same materialization/version must fail.
- Define deterministic rebuild and compatibility for old captures, existing projections and changed parser versions.
- Preserve existing Artifact bodies/revisions, citation identity, ranking and PreparedContext behavior.
- Verify Codex, Claude Code, WorkBuddy and other host payload contracts independently.
- Any public contract change starts in openapi/powercontext.yaml, regenerates clients and defines backward compatibility.
- Test raw readback, all extraction input carriers, replay conflicts, unknown boundaries and historical rebuild.

Open a separate production issue with these requirements and the specific reproduced case IDs; coordinate with #2074.
"""


def save_report(directory: Path, report: dict[str, Any]) -> None:
    directory.mkdir(parents=True, exist_ok=False)
    write_json(directory / "report.json", report)
    lines = [
        "# Capture ownership evaluation",
        "",
        f"Decision: `{report['decision']}`",
        "",
        f"Generation: `{report['generation_mode']}`. Corpus: `{report['corpus']}`.",
        "",
        f"Run SHA-256: `{report['run_sha256']}`",
        "",
        "| Case | Arm | Repeat | Status | Candidates | Reviewed | Critical | Retained / required |",
        "|---|---|---:|---|---:|---|---:|---|",
    ]
    for row in report["rows"]:
        lines.append(
            f"| {row['case_id']} | {row['arm']} | {row['repetition']} | {row['status']} | "
            f"{row['candidate_count']} | {row['review_complete']} | {row['critical_candidates']} | "
            f"{row['retained_current_claims']} / {row['required_current_claims']} |"
        )
    lines += ["", "## Limitations", ""] + [f"- {item}" for item in report["limitations"]]
    (directory / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    if report["decision"] == "projection_follow_up_required":
        (directory / "follow-up-issue.md").write_text(FOLLOW_UP, encoding="utf-8")


def report_from_run(private_dir: Path, output: Path, review_path: Path | None) -> dict[str, Any]:
    manifest = Manifest.model_validate(read_json(private_dir / "manifest.json"))
    lock = read_json(private_dir / "lock.json")
    run = read_json(private_dir / "run.json")
    reviews = None if review_path is None else ReviewFile.model_validate(read_json(review_path))
    if not isinstance(lock, dict) or not isinstance(run, dict):
        raise TypeError("saved lock and run must be JSON objects")
    report = summarize(manifest, cast(dict[str, Any], lock), cast(dict[str, Any], run), reviews)
    save_report(output, report)
    return report
