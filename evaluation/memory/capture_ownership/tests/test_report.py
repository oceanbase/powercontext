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

from copy import deepcopy

import pytest

from powercontext_eval_capture_ownership.models import ARMS, Manifest, ReviewFile, RunConfig, fingerprint
from powercontext_eval_capture_ownership.report import review_template, summarize


def experiment(manifest, *, contaminated=False, mode="live"):
    manifest = Manifest.model_validate(
        manifest.model_dump(mode="json") | {"cases": [manifest.cases[0].model_dump(mode="json")]}
    )
    case = manifest.cases[0]
    config = RunConfig(schema_version="capture-ownership.config.v1")
    lock = {
        "manifest_sha256": fingerprint(manifest.model_dump(mode="json")),
        "config": config.model_dump(mode="json"),
        "code_sha": "a" * 40,
        "prompt_version": "powercontext.atomic-memory.extract.coding.v1",
        "prompt_sha256": "b" * 64,
        "file_sha256": {},
        "decision_policy": {"critical_repetitions": 2},
    }
    rows = []
    for repetition in range(2):
        for arm in ARMS:
            candidates = [{"kind": "constraint", "text": case.claims[-1].text, "evidence_ids": ["source-1"]}]
            if contaminated and arm == ARMS[0]:
                candidates.append({"kind": "fact", "text": case.claims[0].text, "evidence_ids": ["source-1"]})
            rows.append(
                {
                    "case_id": case.case_id,
                    "arm": arm,
                    "repetition": repetition,
                    "mode": mode,
                    "status": "completed",
                    "turn_status": "eligible",
                    "turn_reason": "validated_host_envelope",
                    "parser_matches_ground_truth": True,
                    "source_sha256": "c" * 64,
                    "materialization_sha256": "d" * 64,
                    "source_position": 1,
                    "evidence_id": "source-1",
                    "input": {"raw": "PRIVATE_SOURCE"},
                    "input_sha256": fingerprint({"raw": "PRIVATE_SOURCE"}),
                    "candidates": candidates,
                    "output_sha256": fingerprint(candidates),
                    "occupancy": {
                        "labeled_utf8_bytes": {"current": 50, "history": 0},
                        "text_carriers": 1,
                        "serialized_utf8_bytes": 100,
                    },
                    "estimated_input_tokens": 10,
                    "generation_seconds": 0.1,
                    "provider_usage": None,
                }
            )
    run = {"complete": True, "lock_sha256": fingerprint(lock), "mode": mode, "rows": rows}
    review = review_template(run)
    for row in review["cases"]:
        row.update(complete=True, reviewer="test-reviewer")
        for index, candidate in enumerate(row["candidates"]):
            candidate.update(
                claim_ids=[case.claims[-1 if index == 0 else 0].claim_id],
                support_complete=True,
                unsupported_claim=False,
                current_user_attribution=index > 0,
            )
    return manifest, lock, run, ReviewFile.model_validate(review)


def test_reproducible_contamination_and_current_retention_require_follow_up(manifest):
    values = experiment(manifest, contaminated=True)
    report = summarize(*values)
    assert report["decision"] == "projection_follow_up_required"
    assert report["reproduced_case_ids"] == [manifest.cases[0].case_id]
    assert report["rows"][0]["false_current_attribution"] == 1
    assert "PRIVATE_SOURCE" not in str(report)
    assert values[0].cases[0].claims[-1].text not in str(report)


def bind_review(run, review):
    """Keep annotations bound when a regression changes saved candidate output."""

    for row, annotation in zip(run["rows"], review["cases"], strict=True):
        row["output_sha256"] = fingerprint(row["candidates"])
        annotation["output_sha256"] = row["output_sha256"]
        for candidate, label in zip(row["candidates"], annotation["candidates"], strict=True):
            label["candidate_sha256"] = fingerprint(candidate)
    review["run_sha256"] = fingerprint(run)
    return ReviewFile.model_validate(review)


@pytest.mark.parametrize("support", ["history", "false_attribution", "unsupported"])
def test_follow_up_requires_ownership_contamination_not_unsupported_output_alone(manifest, support):
    manifest, lock, run, review = experiment(manifest, contaminated=True)
    labels = review.model_dump(mode="json")
    for row, annotation in zip(run["rows"], labels["cases"], strict=True):
        if row["arm"] != ARMS[0]:
            continue
        label = annotation["candidates"][1]
        label["current_user_attribution"] = support == "false_attribution"
        if support == "unsupported":
            row["candidates"][1]["text"] = "The invented service identifier is ZETA-99."
            label.update(claim_ids=[], support_span_ids=[], unsupported_claim=True)
        elif support == "false_attribution":
            # Mixed support becomes false attribution only with the explicit label.
            label["support_span_ids"] = list(manifest.cases[0].claims[-1].span_ids)
    report = summarize(manifest, lock, run, bind_review(run, labels))
    owned = support != "unsupported"
    assert report["decision"] == ("projection_follow_up_required" if owned else "insufficient_evidence")
    assert report["reproduced_case_ids"] == ([manifest.cases[0].case_id] if owned else [])
    control = report["totals_by_arm"][ARMS[0]]
    assert control["critical_candidates"] == 2
    assert control["ownership_contamination_candidates"] == (2 if owned else 0)
    assert all(row["unsupported_candidates"] == int(not owned) for row in report["rows"] if row["arm"] == ARMS[0])
    assert all(
        row["ownership_contamination_candidates"] == int(owned) for row in report["rows"] if row["arm"] == ARMS[0]
    )


def test_one_ownership_failure_and_one_hallucination_do_not_establish_reproduction(manifest):
    manifest, lock, run, review = experiment(manifest, contaminated=True)
    labels = review.model_dump(mode="json")
    for row, annotation in zip(run["rows"], labels["cases"], strict=True):
        if row["arm"] == ARMS[0] and row["repetition"] == 1:
            row["candidates"][1]["text"] = "The invented service identifier is ZETA-99."
            annotation["candidates"][1].update(
                claim_ids=[], support_span_ids=[], unsupported_claim=True, current_user_attribution=False
            )
    report = summarize(manifest, lock, run, bind_review(run, labels))
    assert report["decision"] == "insufficient_evidence"
    assert report["reproduced_case_ids"] == []


@pytest.mark.parametrize(
    "case_id,literal,changed",
    [
        ("history-before-current", "eu-west-3", "eu-west-30"),
        ("history-before-current", "eu-west-3", "eu-west-3-old"),
        ("history-before-current", "eu-west-3", "eu-west-3.example"),
        ("history-before-current", "eu-west-3", "prefix-eu-west-3"),
        ("quoted-inline-tags", "Python 3.12", "Python 3.120"),
        ("quoted-inline-tags", "Python 3.12", "Python 3.12.1"),
        ("exact-command-path-config", "/srv/atlas/api", "/srv/atlas/api-old"),
        ("exact-command-path-config", "/srv/atlas/api", "/srv/atlas/api/sub"),
        ("exact-command-path-config", "/srv/atlas/api", "/srv/atlas/api.py"),
        ("exact-command-path-config", "ATLAS_TLS_VERIFY=true", "ATLAS_TLS_VERIFY=trueish"),
        ("exact-command-path-config", "uv run pytest tests/integration -q", "uv run pytest tests/integration -q-extra"),
    ],
)
def test_extended_values_do_not_satisfy_exact_current_literal_retention(manifest, case_id, literal, changed):
    case = next(case for case in manifest.cases if case.case_id == case_id)
    manifest, lock, run, review = experiment(manifest.model_copy(update={"cases": (case,)}))
    for row in run["rows"]:
        row["candidates"][0]["text"] = row["candidates"][0]["text"].replace(literal, changed)
    # A mistaken support annotation must not bypass the deterministic literal check.
    report = summarize(manifest, lock, run, bind_review(run, review.model_dump(mode="json")))
    assert report["decision"] == "insufficient_evidence"
    assert all(row["exact_literal_failures"] == 1 for row in report["rows"])
    assert all(row["required_current_retention"] == 0 for row in report["rows"])


@pytest.mark.parametrize(
    "text",
    ["Use `eu-west-3`.", "Use (eu-west-3).", "区域固定为eu-west-3。", "eu-west-3, for Atlas.", "eu-west-3."],
)
def test_complete_current_values_retain_credit_with_quotes_punctuation_and_cjk(manifest, text):
    manifest, lock, run, review = experiment(manifest)
    for row in run["rows"]:
        row["candidates"][0]["text"] = text
    report = summarize(manifest, lock, run, bind_review(run, review.model_dump(mode="json")))
    assert report["decision"] == "no_production_change"
    assert all(row["exact_literal_failures"] == 0 for row in report["rows"])
    assert all(row["required_current_retention"] == 1 for row in report["rows"])


def test_extended_current_value_in_paired_arm_cannot_qualify_a_follow_up(manifest):
    manifest, lock, run, review = experiment(manifest, contaminated=True)
    for row in run["rows"]:
        if row["arm"] == ARMS[1]:
            row["candidates"][0]["text"] = row["candidates"][0]["text"].replace("eu-west-3", "eu-west-30")
    report = summarize(manifest, lock, run, bind_review(run, review.model_dump(mode="json")))
    assert report["decision"] == "insufficient_evidence"
    assert report["reproduced_case_ids"] == []


def test_a_valid_current_candidate_does_not_hide_another_candidates_literal_failure(manifest):
    manifest, lock, run, review = experiment(manifest)
    labels = review.model_dump(mode="json")
    for row, annotation in zip(run["rows"], labels["cases"], strict=True):
        candidate = deepcopy(row["candidates"][0])
        candidate["text"] = candidate["text"].replace("eu-west-3", "eu-west-4")
        row["candidates"].append(candidate)
        label = deepcopy(annotation["candidates"][0])
        label["candidate_index"] = 1
        annotation["candidates"].append(label)
    report = summarize(manifest, lock, run, bind_review(run, labels))
    assert all(row["required_current_retention"] == 1 for row in report["rows"])
    assert all(row["exact_literal_failures"] == 1 for row in report["rows"])
    assert report["decision"] == "insufficient_evidence"


def test_unreviewed_or_offline_outputs_cannot_claim_clean_generation(manifest):
    manifest, lock, run, review = experiment(manifest)
    unreviewed = summarize(manifest, lock, run)
    assert unreviewed["decision"] == "insufficient_evidence"
    assert all(row["ownership_contamination_candidates"] is None for row in unreviewed["rows"])
    assert all(total["ownership_contamination_candidates"] is None for total in unreviewed["totals_by_arm"].values())
    run["mode"] = "offline"
    review = ReviewFile.model_validate(review.model_dump(mode="json") | {"run_sha256": fingerprint(run)})
    assert summarize(manifest, lock, run, review)["decision"] == "insufficient_evidence"


def test_clean_outputs_still_need_a_positive_control(manifest):
    manifest, lock, run, review = experiment(manifest)
    assert summarize(manifest, lock, run, review)["decision"] == "no_production_change"
    changed = manifest.model_dump(mode="json")
    for claim in changed["cases"][0]["claims"]:
        claim["required_current"] = False
    manifest = Manifest.model_validate(changed)
    lock["manifest_sha256"] = fingerprint(manifest.model_dump(mode="json"))
    run["lock_sha256"] = fingerprint(lock)
    review = ReviewFile.model_validate(review.model_dump(mode="json") | {"run_sha256": fingerprint(run)})
    assert summarize(manifest, lock, run, review)["decision"] == "insufficient_evidence"


@pytest.mark.parametrize(
    "change", ["missing_row", "changed_output", "unknown_evidence", "changed_input", "changed_identity"]
)
def test_report_rejects_unpaired_or_changed_evidence(manifest, change):
    manifest, lock, run, _ = experiment(manifest, contaminated=True)
    if change == "missing_row":
        run["rows"].pop()
    elif change == "changed_output":
        run["rows"][0]["candidates"][0]["text"] = "tampered"
    elif change == "changed_input":
        run["rows"][0]["input"] = {"changed": True}
    elif change == "changed_identity":
        run["rows"][1]["source_sha256"] = "f" * 64
    else:
        run["rows"][0]["candidates"][0]["evidence_ids"] = ["unavailable"]
        run["rows"][0]["output_sha256"] = fingerprint(run["rows"][0]["candidates"])
    with pytest.raises(ValueError):
        summarize(manifest, lock, run)


@pytest.mark.parametrize(
    "field,value",
    [
        ("turn_reason", "PRIVATE_SOURCE"),
        ("provider_usage", {"api_key": "PRIVATE_SECRET"}),
        ("occupancy", {"raw": "PRIVATE_SOURCE"}),
        ("source_sha256", "PRIVATE_SOURCE"),
        ("generation_seconds", float("nan")),
    ],
)
def test_private_content_cannot_be_smuggled_through_aggregate_fields(manifest, field, value):
    manifest, lock, run, _ = experiment(manifest)
    run["rows"][0][field] = value
    with pytest.raises(ValueError):
        summarize(manifest, lock, run)


def test_annotation_is_bound_to_exact_output_and_complete_support(manifest):
    manifest, lock, run, review = experiment(manifest, contaminated=True)
    value = deepcopy(review.model_dump(mode="json"))
    value["cases"][0]["candidates"][0]["candidate_sha256"] = "f" * 64
    with pytest.raises(ValueError):
        summarize(manifest, lock, run, ReviewFile.model_validate(value))
    value = review.model_dump(mode="json")
    value["cases"][0]["candidates"][0]["support_complete"] = False
    assert summarize(manifest, lock, run, ReviewFile.model_validate(value))["decision"] == "insufficient_evidence"


def test_review_can_reference_frozen_spans_outside_retention_targets(manifest):
    manifest, lock, run, review = experiment(manifest)
    value = review.model_dump(mode="json")
    # A descriptive claim may combine a quotation with its current-user introduction.
    # Such support is mixed; it is not automatically a falsely attributed new preference.
    value["cases"][0]["candidates"][0]["support_span_ids"] = [manifest.cases[0].claims[0].span_ids[0]]
    report = summarize(manifest, lock, run, ReviewFile.model_validate(value))
    assert report["rows"][0]["support_categories"] == {"mixed": 1}
    assert report["rows"][0]["critical_candidates"] == 0
    value["cases"][0]["candidates"][0]["support_span_ids"] = ["unknown-span"]
    with pytest.raises(ValueError):
        summarize(manifest, lock, run, ReviewFile.model_validate(value))


@pytest.mark.parametrize("usage_mode", ["complete", "partial", "unknown", "input_only", "zero"])
def test_failed_requests_contribute_known_usage_without_claiming_unknown_usage(manifest, usage_mode):
    manifest, lock, run, _ = experiment(manifest)
    config = RunConfig.model_validate(lock["config"]).model_copy(
        update={"input_cny_per_million": 0.15, "output_cny_per_million": 1.5}
    )
    lock["config"] = config.model_dump(mode="json")
    run["lock_sha256"] = fingerprint(lock)
    for index, row in enumerate(run["rows"]):
        row.update(status="generation_failed", candidates=[], output_sha256=fingerprint([]))
        row["provider_usage"] = {"requests": 1, "input_tokens": 101, "output_tokens": 7}
        if usage_mode == "unknown" or usage_mode == "partial" and index == 0:
            row["provider_usage"] = None
        elif usage_mode == "input_only":
            row["provider_usage"]["output_tokens"] = None
        elif usage_mode == "zero":
            row["provider_usage"].update(input_tokens=0, output_tokens=0)
    total = summarize(manifest, lock, run)["totals_by_arm"][ARMS[0]]
    complete = usage_mode in {"complete", "zero"}
    assert total["provider_usage_complete"] is complete
    assert total["provider_input_tokens"] == (202 if usage_mode == "complete" else 0 if usage_mode == "zero" else None)
    assert total["provider_output_tokens"] == (14 if usage_mode == "complete" else 0 if usage_mode == "zero" else None)
    known_input = (
        None if usage_mode == "unknown" else 101 if usage_mode == "partial" else 0 if usage_mode == "zero" else 202
    )
    known_output = (
        None
        if usage_mode in {"unknown", "input_only"}
        else 7
        if usage_mode == "partial"
        else 0
        if usage_mode == "zero"
        else 14
    )
    assert total["known_provider_input_tokens"] == known_input
    assert total["known_provider_output_tokens"] == known_output
    if usage_mode == "unknown":
        assert total["known_usage_cost_cny"] is None
    else:
        assert total["known_usage_cost_cny"] == pytest.approx(
            ((known_input or 0) * 0.15 + (known_output or 0) * 1.5) / 1_000_000
        )
