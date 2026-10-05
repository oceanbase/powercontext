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

"""External acceptance remains opt-in when daily tests exclude live models."""

from pathlib import Path

import pytest

pytest_plugins = ["pytester"]


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        ([], {"test_daily"}),
        (["-m", "not zcode_live_model"], {"test_daily"}),
        (["-m", "zcode_host_acceptance and not zcode_live_model"], {"test_controlled"}),
        (["-m", "zcode_live_model"], {"test_live"}),
        (["-m", "not (not zcode_live_model)"], {"test_live"}),
        (["--run-zcode-acceptance"], {"test_daily", "test_controlled"}),
    ],
)
def test_external_host_selection(pytester: pytest.Pytester, arguments: list[str], expected: set[str]) -> None:
    pytester.makeconftest(Path(__file__).with_name("conftest.py").read_text(encoding="utf-8"))
    pytester.makeini(
        "[pytest]\nmarkers =\n    zcode_host_acceptance: external host\n    zcode_live_model: live inference\n"
    )
    pytester.makepyfile(
        test_selection="""
        import pytest

        def test_daily():
            raise AssertionError("collection must not execute a test")

        @pytest.mark.zcode_host_acceptance
        def test_controlled():
            raise AssertionError("collection must not launch a host")

        @pytest.mark.zcode_host_acceptance
        @pytest.mark.zcode_live_model
        def test_live():
            raise AssertionError("collection must not contact a model")
        """
    )
    result = pytester.runpytest_subprocess("--collect-only", "-q", *arguments)
    assert result.ret == 0
    collected = {line.split("::")[-1] for line in result.stdout.lines if line.startswith("test_selection.py::")}
    assert collected == expected
