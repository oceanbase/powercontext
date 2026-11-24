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

"""Parse Skill tool declarations for inspection and rule comparison.

Commas and whitespace separate rules outside balanced, nestable parentheses.
Constraints and malformed tokens retain their original text.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field

from typing_extensions import override


@dataclass(frozen=True)
class AllowedToolRule:
    """One declared tool and its uninterpreted optional constraint."""

    tool: str
    constraint: str | None
    raw: str = field(compare=False)

    @override
    def __eq__(self, other: object) -> bool:
        if not isinstance(other, AllowedToolRule):
            return NotImplemented
        return (self.tool.casefold(), self.constraint) == (other.tool.casefold(), other.constraint)

    @override
    def __hash__(self) -> int:
        return hash((self.tool.casefold(), self.constraint))


@dataclass(frozen=True)
class AllowedToolsDeclaration:
    """The original declaration with ordered rules and unparsed fragments."""

    raw: str | None
    rules: tuple[AllowedToolRule, ...]
    unparsed: tuple[str, ...]


def parse_allowed_tools(value: str | None, /) -> AllowedToolsDeclaration:
    """Parse a declaration, retaining malformed fragments without repairing them."""

    rules: list[AllowedToolRule] = []
    unparsed: list[str] = []
    for token in _tokens(value or ""):
        rule = _parse_rule(token)
        if rule is None:
            unparsed.append(token)
        else:
            rules.append(rule)
    return AllowedToolsDeclaration(value, tuple(rules), tuple(unparsed))


def _tokens(value: str) -> Iterator[str]:
    start = 0
    depth = 0
    for index, char in enumerate(value):
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        elif depth == 0 and (char == "," or char.isspace()):
            if start < index:
                yield value[start:index]
            start = index + 1
    if start < len(value):
        yield value[start:]


def _parse_rule(token: str) -> AllowedToolRule | None:
    tool, opening, constraint = token.partition("(")
    if not tool or ")" in tool:
        return None
    if not opening:
        return AllowedToolRule(tool, None, token)
    depth = 1
    for index, char in enumerate(constraint):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if depth == 0:
            if index != len(constraint) - 1:
                return None
            return AllowedToolRule(tool, constraint[:-1], token)
    return None


__all__ = ["AllowedToolRule", "AllowedToolsDeclaration", "parse_allowed_tools"]
