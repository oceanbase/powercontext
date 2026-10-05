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

"""Synthetic artifacts test the report gate, never establish model qualification."""

from __future__ import annotations

import importlib.util
import io
import json
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("skill_up_report", Path(__file__).parents[1] / "report.py")
report = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(report)


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.iteration = Path(self.temp.name) / "iteration-1"
        self.iteration.mkdir()
        self.lock = Path(self.temp.name) / "skill-lock.json"
        self.lock.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "source_path": "synthetic-test-skill",
                    "source_commit": "a" * 40,
                    "files": {"SKILL.md": "b" * 64},
                    "content_sha256": "c" * 64,
                }
            ),
            encoding="utf-8",
        )
        self.result = {"schema_version": "v1alpha1", "engine_name": "claude_code", "case_results": []}
        for case_id, turn_count in report.CASE_TURNS.items():
            for arm in report.ARMS:
                passed = arm == "with_skill"
                turns = [
                    {"turn_number": turn, "content": f"Synthetic {case_id} prompt {turn}", "status": "completed"}
                    for turn in range(1, turn_count + 1)
                ]
                self.result["case_results"].append(
                    {
                        "case_id": case_id,
                        "configuration": arm,
                        "status": "PASS" if passed else "FAIL",
                        "turn_results": turns,
                        "grading": {
                            "status": "PASS" if passed else "FAIL",
                            "assertion_results": [
                                {
                                    "text": "Synthetic assertion",
                                    "passed": passed,
                                    "evidence": "Synthetic test data only",
                                }
                            ],
                        },
                    }
                )
                events = []
                for turn in turns:
                    events.append({"type": "user", "message": {"content": turn["content"]}})
                    if passed and turn["turn_number"] == 1:
                        for index, name in enumerate(sorted(report.REQUIRED_CALLS.get(case_id, set()))):
                            events += [
                                {
                                    "type": "assistant",
                                    "message": {
                                        "stop_reason": "tool_use",
                                        "content": [
                                            {
                                                "type": "tool_use",
                                                "id": f"call-{index}",
                                                "name": report.PREFIX + name,
                                                "input": {"scope_id": report.FIXTURE_SCOPE},
                                            }
                                        ],
                                    },
                                },
                                {
                                    "type": "user",
                                    "message": {
                                        "content": [
                                            {"type": "tool_result", "tool_use_id": f"call-{index}", "content": "mocked"}
                                        ]
                                    },
                                },
                            ]
                    events.append(
                        {
                            "type": "assistant",
                            "message": {"stop_reason": "end_turn", "content": [{"type": "text", "text": "Done."}]},
                        }
                    )
                path = self.transcript(case_id, arm)
                path.parent.mkdir(parents=True)
                path.write_text("\n".join(json.dumps(event) for event in events), encoding="utf-8")
        self.write_result()

    def transcript(self, case_id, arm="with_skill"):
        return self.iteration / case_id / arm / "outputs/agent/run/synthetic-session.jsonl"

    def write_result(self):
        (self.iteration / "result.json").write_text(json.dumps(self.result), encoding="utf-8")

    def manifest(self):
        return report.build_manifest(self.iteration, self.lock)

    def test_failed_baseline_is_comparison_data_and_turn_boundaries_ignore_tool_results(self):
        manifest = self.manifest()
        self.assertEqual(manifest["status"], "PASS", manifest["evidence_errors"])
        self.assertEqual(manifest["arms"]["without_skill"]["pass_rate"], 0)
        self.assertEqual(manifest["pass_rate_delta"], 1)
        search = next(case for case in manifest["cases"] if case["case_id"] == "empty-search")
        self.assertEqual(search["turns"][0]["tool_names"], {report.PREFIX + "search_memory": 1})
        self.assertEqual(search["turns"][1]["tool_names"], {})
        markdown = report.render_markdown(manifest)
        for number in range(1, 9):
            self.assertIn(f"**C{number}**", markdown)
        self.assertIn(
            report.PREFIX + "approve_artifact_candidate", manifest["declared_names_without_recorded_verification"]
        )

    def test_missing_baseline_or_transcript_cannot_pass(self):
        self.result["case_results"] = self.result["case_results"][:-1]
        self.write_result()
        self.transcript("ordinary-coding").unlink()
        manifest = self.manifest()
        self.assertEqual(manifest["status"], "FAIL")
        self.assertTrue(any("Missing without_skill" in error for error in manifest["evidence_errors"]))
        self.assertTrue(any("Expected one Claude session" in error for error in manifest["evidence_errors"]))

    def test_unqualified_name_cannot_satisfy_exact_positive_control(self):
        path = self.transcript("explicit-save")
        path.write_text(
            path.read_text(encoding="utf-8").replace(report.PREFIX + "remember_memory", "remember_memory"),
            encoding="utf-8",
        )
        manifest = self.manifest()
        self.assertEqual(manifest["status"], "FAIL")
        self.assertTrue(manifest["evidence_complete"])
        self.assertTrue(any("namespace" in failure for failure in manifest["qualification_failures"]))
        self.assertTrue(any("Required positive calls" in failure for failure in manifest["qualification_failures"]))

    def test_every_scoped_call_is_checked_before_and_after_a_correct_call(self):
        path = self.transcript("explicit-save")
        original = path.read_text(encoding="utf-8")
        for name, arguments, position in (
            ("remember_memory", {"scope_id": "other-project"}, 1),
            ("remember_memory", {"scope_id": "other-project"}, 3),
            ("remember_memory", {}, 1),
            ("get_scope", {"scope_id": "other-project"}, 1),
            ("get_scope", {}, 1),
            ("resolve_scope_binding", {"explicit_scope_id": "other-project"}, 1),
        ):
            with self.subTest(name=name, arguments=arguments, position=position):
                events = [json.loads(line) for line in original.splitlines()]
                extra = json.loads(json.dumps(events[1:3]))
                extra[0]["message"]["content"][0].update(id="extra", name=report.PREFIX + name, input=arguments)
                extra[1]["message"]["content"][0]["tool_use_id"] = "extra"
                events[position:position] = extra
                path.write_text("\n".join(map(json.dumps, events)), encoding="utf-8")
                manifest = self.manifest()
                self.assertTrue(manifest["evidence_complete"], manifest["evidence_errors"])
                self.assertEqual(manifest["status"], "FAIL")
                self.assertTrue(any("Wrong/missing Scope" in failure for failure in manifest["qualification_failures"]))
                case = next(c for c in manifest["cases"] if c["case_id"] == "explicit-save")
                self.assertEqual((case["native_status"], case["status"]), ("PASS", "FAIL"))
                self.assertEqual(manifest["arms"]["with_skill"]["passed"], 4)

    def test_wrong_scope_in_baseline_changes_comparison_without_failing_suite(self):
        text = self.transcript("explicit-save").read_text(encoding="utf-8")
        self.transcript("explicit-save", "without_skill").write_text(
            text.replace(report.FIXTURE_SCOPE, "other-project"), encoding="utf-8"
        )
        row = next(
            r
            for r in self.result["case_results"]
            if r["case_id"] == "explicit-save" and r["configuration"] == "without_skill"
        )
        row["status"] = row["grading"]["status"] = "PASS"
        row["grading"]["assertion_results"][0]["passed"] = True
        self.write_result()
        manifest = self.manifest()
        self.assertEqual(manifest["status"], "PASS", manifest["evidence_errors"])
        self.assertEqual(manifest["arms"]["without_skill"]["passed"], 0)
        case = next(
            c for c in manifest["cases"] if c["case_id"] == "explicit-save" and c["configuration"] == "without_skill"
        )
        self.assertEqual((case["native_status"], case["status"]), ("PASS", "FAIL"))
        self.assertTrue(case["scope_failures"])

    def test_truncated_turn_or_unanswered_tool_cannot_qualify(self):
        original = self.transcript("explicit-save").read_text(encoding="utf-8")
        events = [json.loads(line) for line in original.splitlines()]
        mismatched = json.loads(json.dumps(events))
        mismatched[2]["message"]["content"][0]["tool_use_id"] = "unrelated-call"
        for arm in report.ARMS:
            for description, incomplete in (
                ("ends at tool call", events[:2]),
                ("ends at tool result", events[:3]),
                ("missing tool result", events[:2] + events[3:]),
                ("answer before result", events[:2] + events[3:] + events[2:3]),
                ("wrong result ID", mismatched),
            ):
                with self.subTest(arm=arm, description=description):
                    path = self.transcript("explicit-save", arm)
                    path.write_text("\n".join(map(json.dumps, incomplete)), encoding="utf-8")
                    manifest = self.manifest()
                    self.assertEqual(manifest["status"], "FAIL")
                    self.assertFalse(manifest["evidence_complete"])
                    self.assertTrue(
                        any("No completed assistant response" in error for error in manifest["evidence_errors"])
                    )
                    path.write_text(original, encoding="utf-8")

    def test_engine_error_survives_an_otherwise_passing_grade(self):
        self.result["case_results"][0]["error"] = "synthetic engine failure"
        self.write_result()
        manifest = report.build_manifest(self.iteration, self.lock, engine_exit_code=17)
        self.assertEqual(manifest["status"], "FAIL")
        self.assertEqual(manifest["engine_exit_code"], 17)
        self.assertIn("synthetic engine failure", " ".join(manifest["evidence_errors"]))
        self.assertIn("skill-up exited with code 17", manifest["qualification_failures"])

    def test_behavior_failure_with_complete_artifacts_is_not_incomplete_evidence(self):
        # Mirrors a completed model choosing a native Write instead of the
        # required MCP call. The evidence is complete and the behavior fails.
        path = self.transcript("explicit-save")
        path.write_text(
            path.read_text(encoding="utf-8").replace(report.PREFIX + "remember_memory", "Write"), encoding="utf-8"
        )
        row = next(
            row
            for row in self.result["case_results"]
            if row["case_id"] == "explicit-save" and row["configuration"] == "with_skill"
        )
        row["status"] = row["grading"]["status"] = "FAIL"
        row["grading"]["assertion_results"][0]["passed"] = False
        self.write_result()
        manifest = report.build_manifest(self.iteration, self.lock, engine_exit_code=1)
        self.assertEqual(manifest["status"], "FAIL")
        self.assertTrue(manifest["evidence_complete"])
        self.assertEqual(manifest["evidence_errors"], [])
        self.assertIn("Case/grading failed for explicit-save/with_skill", manifest["qualification_failures"])
        self.assertIn("skill-up exited with code 1", manifest["qualification_failures"])
        self.assertTrue(any("Required positive calls" in failure for failure in manifest["qualification_failures"]))
        self.assertIn("does not establish", manifest["limitations"]["skill_activation"])
        self.assertIn("same neutral argument guidance", manifest["limitations"]["mock_contract"])

    def test_forbidden_call_and_nonzero_runner_are_qualification_failures(self):
        path = self.transcript("explicit-save")
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                report.PREFIX + "remember_memory", report.PREFIX + "publish_artifact"
            ),
            encoding="utf-8",
        )
        manifest = self.manifest()
        self.assertTrue(manifest["evidence_complete"])
        self.assertEqual(manifest["status"], "FAIL")
        self.assertTrue(any("Forbidden calls" in failure for failure in manifest["qualification_failures"]))
        # A nonzero runner must remain fatal even if every grading row passes.
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                report.PREFIX + "publish_artifact", report.PREFIX + "remember_memory"
            ),
            encoding="utf-8",
        )
        manifest = report.build_manifest(self.iteration, self.lock, engine_exit_code=2)
        self.assertTrue(manifest["evidence_complete"])
        self.assertEqual(manifest["status"], "FAIL")
        self.assertEqual(manifest["qualification_failures"], ["skill-up exited with code 2"])

    def test_official_claude_code_aliases_preserve_the_recorded_name(self):
        for engine_name in ("claude_code", "claude-code"):
            with self.subTest(engine_name=engine_name):
                self.result["engine_name"] = engine_name
                self.write_result()
                manifest = self.manifest()
                self.assertEqual(manifest["status"], "PASS", manifest["evidence_errors"])
                self.assertEqual(manifest["engine_configuration"]["engine_name"], engine_name)

    def test_setup_error_shape_from_native_run_remains_a_failed_report(self):
        # v0.12.0 omits turn_results and emits null grading when MCP setup
        # fails before a model runs. This synthetic regression mirrors that
        # structure without presenting it as live model evidence.
        for row in self.result["case_results"]:
            row.update(status="ERROR", grading=None, error="synthetic MCP installation failure", turns=0)
            del row["turn_results"]
        self.write_result()
        for path in self.iteration.rglob("*.jsonl"):
            path.unlink()
        manifest = self.manifest()
        self.assertEqual(manifest["status"], "FAIL")
        self.assertFalse(manifest["evidence_complete"])
        self.assertTrue(all(case["error"] == "synthetic MCP installation failure" for case in manifest["cases"]))
        self.assertFalse(any("Unsupported/unverified engine" in error for error in manifest["evidence_errors"]))
        self.assertIn("synthetic MCP installation failure", report.render_markdown(manifest))

    def test_long_artifact_paths_can_be_read_hashed_and_reported(self):
        # Relative Windows paths at or above MAX_PATH can be listed by glob
        # but fail to open unless the report normalizes them for native I/O.
        long_root = Path(self.temp.name) / ("long-artifact-path-" * 5) / ("nested-" * 12) / "iteration-1"
        long_tree = long_root.parent.parent
        self.assertTrue(long_tree.absolute().is_relative_to(Path(self.temp.name).absolute()))
        self.addCleanup(shutil.rmtree, report.native_path(long_tree))
        shutil.copytree(self.iteration, report.native_path(long_root))
        manifest = report.build_manifest(long_root, self.lock)
        self.assertEqual(manifest["status"], "PASS", manifest["evidence_errors"])
        self.assertEqual(manifest["source_result"], str(long_root / "result.json"))
        for case in manifest["cases"]:
            for turn in case["turns"]:
                self.assertEqual(len(turn["transcript_sha256"]), 64)
                self.assertFalse(Path(turn["transcript"]).is_absolute())
        with patch.object(sys, "argv", ["report.py", "--iteration", str(long_root), "--skill-lock", str(self.lock)]):
            with redirect_stdout(io.StringIO()):
                self.assertEqual(report.main(), 0)
        saved = json.loads(report.native_path(long_root / "qualification.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["status"], "PASS")

    def test_empty_calls_and_missing_prompt_boundaries_cannot_pass(self):
        for path in self.iteration.rglob("*.jsonl"):
            path.write_text(
                json.dumps({"type": "assistant", "message": {"content": "Synthetic answer only"}}), encoding="utf-8"
            )
        manifest = self.manifest()
        self.assertEqual(manifest["status"], "FAIL")
        self.assertTrue(any("Zero recorded calls" in failure for failure in manifest["qualification_failures"]))
        self.assertTrue(any("prompt boundaries" in error for error in manifest["evidence_errors"]))

    def test_case_mismatch_and_contradictory_grading_cannot_pass(self):
        self.result["case_results"][0]["grading"]["assertion_results"][0]["passed"] = False
        self.result["case_results"][1]["case_id"] = "wrong-case"
        self.write_result()
        manifest = self.manifest()
        self.assertEqual(manifest["status"], "FAIL")
        self.assertTrue(any("Unexpected case" in error for error in manifest["evidence_errors"]))
        self.assertTrue(any("contradicts grading" in error for error in manifest["evidence_errors"]))


if __name__ == "__main__":
    unittest.main()
