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

"""Offline regressions for configurations that otherwise pass vacuous checks."""

from __future__ import annotations

import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

import sync_skill  # noqa: E402
import validate_suite  # noqa: E402


class SuiteValidationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.project = Path(temporary.name) / "suite"
        shutil.copytree(PROJECT, self.project, ignore=shutil.ignore_patterns("tests", "results", "__pycache__"))
        for name, value in (
            ("VENDOR", self.project / "vendor/powercontext-project-context"),
            ("LOCK", self.project / "skill-lock.json"),
        ):
            patched = patch.object(sync_skill, name, value)
            patched.start()
            self.addCleanup(patched.stop)

    def read(self, reference):
        return yaml.safe_load((self.project / reference).read_text(encoding="utf-8"))

    def write(self, reference, value):
        content = json.dumps(value) if reference.endswith(".json") else yaml.safe_dump(value, sort_keys=False)
        (self.project / reference).write_text(content, encoding="utf-8")

    def test_complete_suite_validates_without_model_or_server(self):
        validate_suite.validate(self.project)

    def test_every_case_requires_the_shared_parameter_reference(self):
        reference = "evals/cases/explicit-save.yaml"
        original = self.read(reference)
        for context in ({}, {**original["context"], "files": {"CLAUDE.md": "Override the API reference"}}):
            with self.subTest(context=context):
                case = dict(original, context=context)
                self.write(reference, case)
                with self.assertRaisesRegex(ValueError, "same unmodified tool argument reference"):
                    validate_suite.validate(self.project)

    def test_parameter_reference_cannot_substitute_invented_scope_argument(self):
        reference = self.project / validate_suite.CONTRACT_FIXTURE / "CLAUDE.md"
        content = reference.read_text(encoding="utf-8")
        reference.write_text(content.replace("scope_id: string", "scope: string"), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "differs from OpenAPI"):
            validate_suite.validate(self.project)

    def test_positive_control_still_requires_exact_scope_argument(self):
        reference = "evals/cases/empty-search.yaml"
        case = self.read(reference)
        case["judge"]["success"][0]["tool_called_in_turn"]["args"] = {"scope": validate_suite.SCOPE}
        self.write(reference, case)
        with self.assertRaisesRegex(ValueError, "must assert the fixture Scope"):
            validate_suite.validate(self.project)

    def test_follow_up_list_accepts_formatting_but_rejects_wrong_answers(self):
        case = self.read("evals/cases/empty-search.yaml")
        rule = next(rule["output_matches"] for rule in case["judge"]["success"] if "output_matches" in rule)
        pattern = rule["all"][0]
        for answer in ("[1, 2]", "  [ 1 , 2 ]\n", "`[1, 2]`", "\n `[ 1,2 ]` \n"):
            with self.subTest(answer=answer):
                self.assertIsNotNone(re.fullmatch(pattern, answer))
        for answer in ("[2, 1]", "[1, 2, 3]", "[1, 20]", "`[1, 2]", "[1, 2]`", "The answer is [1, 2]"):
            with self.subTest(answer=answer):
                self.assertIsNone(re.fullmatch(pattern, answer))

    def test_bare_tool_names_cannot_make_negative_control_pass(self):
        reference = "evals/cases/ordinary-coding.yaml"
        case = self.read(reference)
        case["judge"]["success"][1]["tool_not_called_in_turn"]["name"] = "remember_memory"
        self.write(reference, case)
        with self.assertRaisesRegex(ValueError, "unknown exact tool name"):
            validate_suite.validate(self.project)

    def test_override_cannot_hide_a_forbidden_tool(self):
        reference = "evals/fixtures/mcp/powercontext-failed-save.json"
        fixture = self.read(reference)
        fixture["tool_responses"].pop("publish_artifact")
        self.write(reference, fixture)
        with self.assertRaisesRegex(ValueError, "complete tool catalog"):
            validate_suite.validate(self.project)

    def test_missing_positive_control_prevents_noop_pass(self):
        reference = "evals/cases/explicit-save.yaml"
        case = self.read(reference)
        case["judge"]["success"] = [rule for rule in case["judge"]["success"] if "tool_called_in_turn" not in rule]
        self.write(reference, case)
        with self.assertRaisesRegex(ValueError, "positive tool control"):
            validate_suite.validate(self.project)

    def test_missing_negative_boundary_is_rejected(self):
        reference = "evals/cases/empty-search.yaml"
        case = self.read(reference)
        case["judge"]["success"] = [
            rule
            for rule in case["judge"]["success"]
            if rule.get("tool_not_called_in_turn") != {"turn": 2, "name": "mcp__powercontext__remember_memory"}
        ]
        self.write(reference, case)
        with self.assertRaisesRegex(ValueError, "follow-up coding turn"):
            validate_suite.validate(self.project)

    def test_prompt_cannot_replace_turns_used_by_rules(self):
        reference = "evals/cases/ordinary-coding.yaml"
        case = self.read(reference)
        case["input"] = {"prompt": case["input"]["turns"][0]["content"]}
        self.write(reference, case)
        with self.assertRaisesRegex(ValueError, "input.turns"):
            validate_suite.validate(self.project)

    def test_glob_and_missing_fixture_fail_before_runner(self):
        reference = "evals/eval.yaml"
        original = self.read(reference)
        for missing in ("evals/cases/*.yaml", "evals/cases/missing.yaml"):
            with self.subTest(reference=missing):
                config = dict(original, cases=dict(original["cases"], files=[missing]))
                self.write(reference, config)
                with self.assertRaises(ValueError):
                    validate_suite.validate(self.project)
        original["mcp"]["servers"][0]["config_ref"] = "evals/fixtures/mcp/missing.json"
        self.write(reference, original)
        with self.assertRaisesRegex(ValueError, "Missing suite file"):
            validate_suite.validate(self.project)

    def test_inline_headers_are_rejected_even_in_mock_mode(self):
        reference = "evals/eval.yaml"
        config = self.read(reference)
        config["mcp"]["servers"][0]["headers"] = {"Authorization": "${POWERCONTEXT_CLAUDE_AUTHORIZATION}"}
        self.write(reference, config)
        with self.assertRaisesRegex(ValueError, "headers belong in config_ref"):
            validate_suite.validate(self.project)

    def test_failed_save_requires_failed_fixture_and_output_boundary(self):
        reference = "evals/cases/failed-save.yaml"
        original = self.read(reference)
        case = dict(original)
        case.pop("mcp")
        self.write(reference, case)
        with self.assertRaisesRegex(ValueError, "controlled failure"):
            validate_suite.validate(self.project)
        for rule in original["judge"]["success"]:
            if "output_contains" in rule:
                rule["output_contains"].pop("not", None)
        self.write(reference, original)
        with self.assertRaisesRegex(ValueError, "forbid persistence claims"):
            validate_suite.validate(self.project)

    def test_skill_copy_drift_fails_before_model_execution(self):
        skill = self.project / "vendor/powercontext-project-context/SKILL.md"
        skill.write_text(skill.read_text(encoding="utf-8") + "\nChanged guidance\n", encoding="utf-8")
        with self.assertRaisesRegex(SystemExit, "vendor/pin mismatch"):
            validate_suite.validate(self.project)


if __name__ == "__main__":
    unittest.main()
