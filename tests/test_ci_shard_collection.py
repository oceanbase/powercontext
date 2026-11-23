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

"""CI partitions preserve daily test coverage across separate pytest processes."""

from pathlib import Path

import pytest

pytest_plugins = ["pytester"]


@pytest.fixture
def collection_suite(pytester: pytest.Pytester) -> pytest.Pytester:
    pytester.makeconftest(Path(__file__).with_name("conftest.py").read_text(encoding="utf-8"))
    pytester.makeini("[pytest]\nmarkers =\n    zcode_host_acceptance: external host\n")
    source = """
        import pytest

        @pytest.mark.parametrize("case", range(12))
        def test_daily(case):
            raise AssertionError("collection must not execute a test")

        @pytest.mark.zcode_host_acceptance
        def test_external_host():
            raise AssertionError("daily collection must not launch a host")
    """
    pytester.makepyfile(test_alpha=source, test_beta=source)
    return pytester


def test_two_shards_are_disjoint_complete_and_independent_of_collection_order(
    collection_suite: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
):
    def collect(*arguments: str, seed: str = "0") -> set[str]:
        monkeypatch.setenv("PYTHONHASHSEED", seed)
        result = collection_suite.runpytest_subprocess("--collect-only", "-q", *arguments)
        assert result.ret == 0
        return {line for line in result.stdout.lines if line.startswith(("test_alpha.py::", "test_beta.py::"))}

    all_cases = collect()
    assert len(all_cases) == 24
    assert all("test_daily[" in node for node in all_cases)
    shards = [collect("--ci-shard-count=2", f"--ci-shard-index={index}", seed=str(index + 1)) for index in range(2)]
    assert all(shards)
    assert shards[0].isdisjoint(shards[1])
    assert shards[0] | shards[1] == all_cases
    for index, shard in enumerate(shards):
        assert collect("test_beta.py", "test_alpha.py", "--ci-shard-count=2", f"--ci-shard-index={index}") == shard


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (["--ci-shard-count=2"], "must be provided together"),
        (["--ci-shard-index=0"], "must be provided together"),
        (["--ci-shard-count=0", "--ci-shard-index=0"], "must be at least 1"),
        (["--ci-shard-count=2", "--ci-shard-index=-1"], "must be between 0"),
        (["--ci-shard-count=2", "--ci-shard-index=2"], "must be between 0"),
    ],
)
def test_invalid_shard_arguments_fail_clearly(collection_suite: pytest.Pytester, arguments: list[str], message: str):
    result = collection_suite.runpytest_subprocess("--collect-only", "-q", *arguments)
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    assert message in result.stderr.str()
