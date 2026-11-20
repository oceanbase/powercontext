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

"""Failed xdist cases retain their identity and existing structured diagnostics."""

import json
import os
from pathlib import Path

import pytest

pytest_plugins = ["pytester"]


def test_xdist_failure_is_retained_with_its_existing_error_classification(pytester, monkeypatch):
    scripts = Path(__file__).resolve().parents[1] / ".github" / "scripts"
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(filter(None, (str(scripts), os.environ.get("PYTHONPATH")))))
    monkeypatch.setenv("RUNNER_TEMP", str(pytester.path / "runner-temp"))
    pytester.makepyfile(
        """
        import logging

        def test_failure():
            logging.getLogger("fixture").warning(
                "controlled write failure",
                extra={"error_code": "fixture_timeout", "exception_type": "TimeoutError",
                       "unlisted_detail": "do-not-copy-this-field"},
            )
            assert False, "controlled test failure"
        """
    )
    result = pytester.runpytest_subprocess("-n", "2", "-p", "pytest_ci_diagnostics", "-q")
    assert result.ret == pytest.ExitCode.TESTS_FAILED
    evidence = pytester.path / "runner-temp" / "pytest-diagnostics" / "failures.jsonl"
    assert evidence.is_file(), "The controller must retain a failed worker's report before the job exits"
    reports = [json.loads(line) for line in evidence.read_text().splitlines()]
    assert len(reports) == 1
    report = reports[0]
    assert report["nodeid"].endswith("::test_failure")
    assert report["when"] == "call"
    assert "controlled test failure" in report["longrepr"]
    section = next(text for title, text in report["sections"] if title == "CI structured captured logs")
    records = json.loads(section)
    assert records[0]["error_code"] == "fixture_timeout"
    assert records[0]["exception_type"] == "TimeoutError"
    assert "unlisted_detail" not in records[0]
    assert "do-not-copy-this-field" not in section
