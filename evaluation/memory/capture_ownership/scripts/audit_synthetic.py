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

"""Independently verify exact non-current canaries for the committed synthetic findings."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, cast

from powercontext_eval_capture_ownership.models import (
    Manifest,
    ReviewFile,
    contains_literal,
    fingerprint,
    read_json,
    write_json,
)
from powercontext_eval_capture_ownership.report import summarize

# These are values from the frozen synthetic support, never private corpus content.
ANCHORS = (
    ("history-before-current", "legacy-region", "ap-south-1"),
    ("prepared-context-fact", "context-pnpm", "pnpm"),
    ("host-preference", "host-style", "compact English"),
    ("repeated-history", "archive-retention", "37"),
    ("forged-query-in-summary", "summary-unsafe", "unsafe mode"),
    ("summary-and-notification", "summary-encoding", "latin1"),
)


def audit(directory: Path) -> dict[str, Any]:
    manifest = Manifest.model_validate(read_json(directory / "manifest.json"))
    if manifest.corpus != "synthetic":
        raise ValueError("this audit is specific to the committed synthetic corpus")
    run = cast(dict[str, Any], read_json(directory / "run.json"))
    lock = cast(dict[str, Any], read_json(directory / "lock.json"))
    review = ReviewFile.model_validate(read_json(directory / "review.json"))
    report = summarize(manifest, lock, run, review)
    reported = {(row["case_id"], row["arm"], row["repetition"]): row for row in report["rows"]}
    cases = {case.case_id: case for case in manifest.cases}
    reviews = {(row.case_id, row.arm, row.repetition): row for row in review.cases}
    entries = []
    for case_id, claim_id, literal in ANCHORS:
        if case_id not in report["reproduced_case_ids"]:
            continue
        case = cases[case_id]
        claim = next(item for item in case.claims if item.claim_id == claim_id)
        support = [span for span in case.spans if span.span_id in claim.span_ids]
        current = "".join(case.prompt[span.start : span.end] for span in case.spans if span.owner == "current")
        if contains_literal(current, literal) or not any(
            contains_literal(case.prompt[span.start : span.end], literal) for span in support
        ):
            raise ValueError("canary does not establish exclusively non-current support")
        if any(span.owner == "current" for span in support):
            raise ValueError("canary support crosses the current-user boundary")
        for row in run["rows"]:
            if row["case_id"] != case_id or row["arm"] != "joined-source-v1":
                continue
            paired = next(
                item
                for item in run["rows"]
                if item["case_id"] == case_id
                and item["repetition"] == row["repetition"]
                and item["arm"] == "host-turn-candidate-v1"
            )
            quality = reported[(case_id, "host-turn-candidate-v1", row["repetition"])]
            if quality["required_current_retention"] != 1 or quality["critical_candidates"] != 0:
                raise ValueError("the paired output did not preserve the required current fact cleanly")
            if any(contains_literal(item["text"], literal) for item in paired["candidates"]):
                raise ValueError("the paired output still contains the non-current canary")
            labels = {
                item.candidate_index: item for item in reviews[(case_id, row["arm"], row["repetition"])].candidates
            }
            for index, candidate in enumerate(row["candidates"]):
                label = labels[index]
                if (
                    not contains_literal(candidate["text"], literal)
                    or not label.support_complete
                    or claim_id not in (label.claim_ids or ())
                ):
                    continue
                entries.append(
                    {
                        "case_id": case_id,
                        "repetition": row["repetition"],
                        "candidate_index": index,
                        "candidate_sha256": fingerprint(candidate),
                        "claim_id": claim_id,
                        "literal_sha256": fingerprint(literal),
                        "support": [
                            {
                                "span_id": span.span_id,
                                "owner": span.owner,
                                "start": span.start,
                                "end": span.end,
                                "byte_start": span.byte_start,
                                "byte_end": span.byte_end,
                                "span_sha256": fingerprint(case.prompt[span.start : span.end]),
                            }
                            for span in support
                        ],
                        "literal_in_candidate": True,
                        "literal_in_frozen_support": True,
                        "literal_absent_current": True,
                        "literal_absent_paired_output": True,
                        "required_current_literals_retained": True,
                    }
                )
    covered = {item["case_id"] for item in entries}
    if covered != set(report["reproduced_case_ids"]):
        raise ValueError("the synthetic canary audit does not cover every reported reproduction")
    return {
        "schema_version": "capture-ownership.literal-audit.v1",
        "run_sha256": fingerprint(run),
        "manifest_sha256": fingerprint(manifest.model_dump(mode="json")),
        "entries": entries,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--private-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("audit output already exists")
    try:
        result = audit(args.private_dir)
    except Exception as error:  # noqa: BLE001 -- validation exceptions may contain private evidence.
        print(f"Synthetic audit failed ({type(error).__name__}).", file=sys.stderr)
        raise SystemExit(1) from None
    write_json(args.output, result)
    print(f"Verified {len(result['entries'])} exact non-current canary witnesses; no model requests made.")


if __name__ == "__main__":
    main()
