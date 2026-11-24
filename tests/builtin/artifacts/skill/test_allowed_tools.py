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

import pytest

from powercontext.builtin.artifacts.skill import AllowedToolRule, parse_allowed_tools


@pytest.mark.parametrize(
    ("raw", "rules", "unparsed"),
    [
        (None, (), ()),
        ("", (), ()),
        (" ,\t\n", (), ()),
        ("Read, Bash(git:*)", (("Read", None), ("Bash", "git:*")), ()),
        ("Bash(git:*)\nRead", (("Bash", "git:*"), ("Read", None)), ()),
        ("WebFetch(domain:a.com,b.com)", (("WebFetch", "domain:a.com,b.com"),), ()),
        ("Bash(git log (HEAD))", (("Bash", "git log (HEAD)"),), ()),
        ("Bash()", (("Bash", ""),), ()),
        ("Read Read", (("Read", None), ("Read", None)), ()),
        ("Bash(git:*", (), ("Bash(git:*",)),
        ("Read) Bash", (("Bash", None),), ("Read)",)),
        ("Bash(git:* Read", (), ("Bash(git:* Read",)),
        ("Read()suffix, Bash", (("Bash", None),), ("Read()suffix",)),
        ("(Read) Bash", (("Bash", None),), ("(Read)",)),
        ("Read)(*) Bash", (("Bash", None),), ("Read)(*)",)),
        ("Read,)(,Bash", (("Read", None),), (")(,Bash",)),
    ],
)
def test_parse_allowed_tools_preserves_rules_and_unparsed_fragments(raw, rules, unparsed) -> None:
    declaration = parse_allowed_tools(raw)

    assert declaration.raw == raw
    assert tuple((rule.tool, rule.constraint) for rule in declaration.rules) == rules
    assert declaration.unparsed == unparsed


def test_rule_comparison_supports_directional_differences() -> None:
    before = parse_allowed_tools("Read Bash(git:*) Read").rules
    after = parse_allowed_tools("bash(git:*) Write").rules

    assert set(after) - set(before) == {AllowedToolRule("Write", None, "Write")}
    assert set(before) - set(after) == {AllowedToolRule("Read", None, "Read")}
    assert before[1] == after[0] and hash(before[1]) == hash(after[0])
    assert after[0].tool == "bash" and after[0].raw == "bash(git:*)"
    assert before[1] != AllowedToolRule("Bash", "GIT:*", "Bash(GIT:*)")
    assert before[0] != "Read"


@pytest.mark.parametrize("raw", ["Read " * 400, "(" * 1_000 + ")" * 1_000, ")" * 2_000, "\x00" * 2_000])
def test_parser_handles_bounded_inputs_without_recursion(raw: str) -> None:
    assert parse_allowed_tools(raw).raw == raw
