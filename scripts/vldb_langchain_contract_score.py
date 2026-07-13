#!/usr/bin/env python3
"""Score PowerMem LangChain contract tests from JUnit XML suites."""

from __future__ import annotations

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class WeightedTest:
    name: str
    title: str
    weight: int
    requires: tuple[str, ...] = ()


WEIGHTED_TESTS = (
    WeightedTest(
        name="test_retrieves_powermem_memories_before_model_call",
        title="Retrieve memory before model call",
        weight=35,
    ),
    WeightedTest(
        name="test_persists_interaction_after_agent_run",
        title="Persist interaction after agent run",
        weight=25,
    ),
    WeightedTest(
        name="test_can_disable_interaction_persistence",
        title="Disable interaction persistence",
        weight=10,
        requires=("test_persists_interaction_after_agent_run",),
    ),
    WeightedTest(
        name="test_search_failure_is_fail_open_by_default",
        title="Fail-open on search error",
        weight=15,
        requires=("test_retrieves_powermem_memories_before_model_call",),
    ),
    WeightedTest(
        name="test_async_agent_uses_powermem_memory",
        title="Async memory injection",
        weight=15,
    ),
)

GATE_TESTS = (
    WeightedTest(
        name="test_public_import_contract",
        title="Public import contract",
        weight=0,
    ),
)

EXPECTED_TOTAL = sum(item.weight for item in WEIGHTED_TESTS)


@dataclass
class CaseResult:
    status: str
    detail: str = ""


@dataclass
class SuiteResult:
    label: str
    path: Path
    exists: bool
    cases: dict[str, CaseResult]
    tests: int
    failures: int
    errors: int
    skipped: int

    @property
    def passed(self) -> int:
        return max(self.tests - self.failures - self.errors - self.skipped, 0)


def parse_suite_arg(raw: str) -> tuple[str, Path]:
    if "=" not in raw:
        raise argparse.ArgumentTypeError(
            f"invalid suite '{raw}', expected LABEL=/path/to/junit.xml"
        )
    label, path = raw.split("=", 1)
    label = label.strip()
    if not label:
        raise argparse.ArgumentTypeError(f"invalid suite '{raw}', label is empty")
    return label, Path(path)


def summarize_case(testcase: ET.Element) -> CaseResult:
    for tag, status in (("failure", "failed"), ("error", "error"), ("skipped", "skipped")):
        node = testcase.find(tag)
        if node is not None:
            message = (node.get("message") or "").strip()
            text = (node.text or "").strip()
            detail = message or text
            if detail:
                detail = " ".join(detail.split())
            return CaseResult(status=status, detail=detail)
    return CaseResult(status="passed")


def load_suite(label: str, path: Path) -> SuiteResult:
    if not path.is_file():
        return SuiteResult(
            label=label,
            path=path,
            exists=False,
            cases={},
            tests=0,
            failures=0,
            errors=0,
            skipped=0,
        )

    root = ET.parse(path).getroot()
    cases: dict[str, CaseResult] = {}
    tests = failures = errors = skipped = 0

    for testcase in root.iter("testcase"):
        tests += 1
        name = testcase.attrib.get("name", "").strip()
        if not name:
            continue
        cases[name] = summarize_case(testcase)

    for testsuite in root.iter("testsuite"):
        failures += int(testsuite.attrib.get("failures", "0") or "0")
        errors += int(testsuite.attrib.get("errors", "0") or "0")
        skipped += int(testsuite.attrib.get("skipped", "0") or "0")

    return SuiteResult(
        label=label,
        path=path,
        exists=True,
        cases=cases,
        tests=tests,
        failures=failures,
        errors=errors,
        skipped=skipped,
    )


def build_scorecard(suites: list[SuiteResult]) -> dict[str, object]:
    breakdown = []
    gate_results = []
    passing_tests: set[str] = set()
    score = 0

    for gate in GATE_TESTS:
        suite_statuses = []
        all_passed = True
        for suite in suites:
            case = suite.cases.get(gate.name)
            if case is None:
                case = CaseResult(status="missing", detail="Test case not found in JUnit XML")
            suite_statuses.append(
                {
                    "suite": suite.label,
                    "status": case.status,
                    "detail": case.detail,
                }
            )
            if case.status != "passed":
                all_passed = False

        if all_passed:
            passing_tests.add(gate.name)
        gate_results.append(
            {
                "name": gate.name,
                "title": gate.title,
                "status": "passed" if all_passed else "failed",
                "suites": suite_statuses,
            }
        )

    for weighted in WEIGHTED_TESTS:
        suite_statuses = []
        all_passed = True
        for suite in suites:
            case = suite.cases.get(weighted.name)
            if case is None:
                case = CaseResult(status="missing", detail="Test case not found in JUnit XML")
            suite_statuses.append(
                {
                    "suite": suite.label,
                    "status": case.status,
                    "detail": case.detail,
                }
            )
            if case.status != "passed":
                all_passed = False

        dependency_failures = [
            requirement
            for requirement in weighted.requires
            if requirement not in passing_tests
        ]
        can_earn = all_passed and not dependency_failures
        if can_earn:
            passing_tests.add(weighted.name)

        earned = weighted.weight if can_earn else 0
        score += earned
        breakdown.append(
            {
                "name": weighted.name,
                "title": weighted.title,
                "weight": weighted.weight,
                "earned": earned,
                "status": "passed" if can_earn else "failed",
                "test_status": "passed" if all_passed else "failed",
                "requires": list(weighted.requires),
                "dependency_failures": dependency_failures,
                "suites": suite_statuses,
            }
        )

    suite_summaries = [
        {
            "label": suite.label,
            "path": str(suite.path),
            "exists": suite.exists,
            "tests": suite.tests,
            "passed": suite.passed,
            "failures": suite.failures,
            "errors": suite.errors,
            "skipped": suite.skipped,
        }
        for suite in suites
    ]

    return {
        "score": score,
        "max_score": EXPECTED_TOTAL,
        "suite_count": len(suites),
        "gates": gate_results,
        "breakdown": breakdown,
        "suites": suite_summaries,
        "rule": (
            "A weighted capability receives credit only when it passes on every "
            "configured Python suite and all declared prerequisite capabilities "
            "also receive credit. Gate tests are reported but do not add points."
        ),
    }


def format_score_text(scorecard: dict[str, object]) -> str:
    lines = [
        "LangChain Contract Score",
        "========================",
        f"score: {float(scorecard['score']):.2f} / {int(scorecard['max_score'])}",
        f"suites: {int(scorecard['suite_count'])}",
        f"rule: {scorecard['rule']}",
        "",
    ]

    for gate in scorecard["gates"]:
        status = "PASS" if gate["status"] == "passed" else "FAIL"
        lines.append(f"{gate['title']}: {status} (gate)")
        for suite in gate["suites"]:
            suffix = f" - {suite['detail']}" if suite["detail"] else ""
            lines.append(f"  {suite['suite']}: {suite['status']}{suffix}")

    for item in scorecard["breakdown"]:
        status = "PASS" if item["status"] == "passed" else "FAIL"
        lines.append(
            f"{item['title']}: {status} ({item['earned']}/{item['weight']})"
        )
        if item["dependency_failures"]:
            lines.append(f"  blocked_by: {', '.join(item['dependency_failures'])}")
        for suite in item["suites"]:
            suffix = f" - {suite['detail']}" if suite["detail"] else ""
            lines.append(f"  {suite['suite']}: {suite['status']}{suffix}")
    return "\n".join(lines)


def format_report_text(scorecard: dict[str, object]) -> str:
    lines = [
        "LangChain Contract Report",
        "=========================",
        f"score: {float(scorecard['score']):.2f} / {int(scorecard['max_score'])}",
        f"rule: {scorecard['rule']}",
        "",
        "Suite Summary",
        "-------------",
    ]

    for suite in scorecard["suites"]:
        if not suite["exists"]:
            lines.append(f"{suite['label']}: missing ({suite['path']})")
            continue
        lines.append(
            f"{suite['label']}: "
            f"passed={suite['passed']}/{suite['tests']}, "
            f"failures={suite['failures']}, "
            f"errors={suite['errors']}, "
            f"skipped={suite['skipped']}"
        )

    lines.extend(["", "Gate Checks", "-----------"])
    for gate in scorecard["gates"]:
        status = "PASS" if gate["status"] == "passed" else "FAIL"
        lines.append(f"{gate['title']} [gate] {status}")
        for suite in gate["suites"]:
            suffix = f" :: {suite['detail']}" if suite["detail"] else ""
            lines.append(f"  - {suite['suite']}: {suite['status']}{suffix}")

    lines.extend(["", "Weighted Breakdown", "------------------"])
    for item in scorecard["breakdown"]:
        status = "PASS" if item["status"] == "passed" else "FAIL"
        lines.append(
            f"{item['title']} [{item['earned']}/{item['weight']}] {status}"
        )
        if item["dependency_failures"]:
            lines.append(f"  - blocked_by: {', '.join(item['dependency_failures'])}")
        for suite in item["suites"]:
            suffix = f" :: {suite['detail']}" if suite["detail"] else ""
            lines.append(f"  - {suite['suite']}: {suite['status']}{suffix}")

    return "\n".join(lines)


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.rstrip() + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Score PowerMem LangChain contract tests from JUnit XML suites."
    )
    parser.add_argument(
        "--suite",
        action="append",
        default=[],
        metavar="LABEL=PATH",
        help="JUnit XML suite to include in the scorecard.",
    )
    parser.add_argument(
        "--score-output",
        type=Path,
        required=True,
        help="Write the concise score summary to this path.",
    )
    parser.add_argument(
        "--report-output",
        type=Path,
        required=True,
        help="Write the detailed contract report to this path.",
    )
    parser.add_argument(
        "--json-output",
        type=Path,
        help="Optional machine-readable JSON output path.",
    )
    args = parser.parse_args()

    if not args.suite:
        raise SystemExit("at least one --suite LABEL=PATH argument is required")

    suites = [load_suite(*parse_suite_arg(raw)) for raw in args.suite]
    scorecard = build_scorecard(suites)

    score_text = format_score_text(scorecard)
    report_text = format_report_text(scorecard)
    write_text(args.score_output, score_text)
    write_text(args.report_output, report_text)

    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(
            json.dumps(scorecard, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    print(score_text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
