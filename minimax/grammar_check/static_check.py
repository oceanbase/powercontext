#!/usr/bin/env python3
"""Grammar check for the ARRAY element-access node, run outside the source tree.

The probe compiles no seekdb sources: it reads the grammar, the token definitions and
the node-type names, so a full bison/flex toolchain is not required.  It answers the
question a reviewer asks first: *is there a node for `col[i]` at all?*

    python grammar_check/static_check.py [--repo <seekdb checkout>] [--json-out file]

Exit code 0 means the parser exposes a dedicated element-access node; 1 means it does
not, and the statement still falls back to the ARRAY literal or fails to parse.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

ITEM_TYPES = "src/query/api/query/parser/ob_item_type.h"
TOKENS = "src/sql/parser/sql_parser_mysql_mode.l"
GRAMMAR = "src/sql/parser/sql_parser_mysql_mode.y"
NONE_TYPES = {"", "T_INVALID"}

ELEMENT_ACCESS_NAMES = (
    "ARRAY_INDEX",
    "ARRAY_ELEMENT",
    "ARRAY_ACCESS",
    "ARRAY_SUBSCRIPT",
    "ELEMENT_REF",
    "ARRAY_ELEM",
)


@dataclass
class Finding:
    node: str = "T_INVALID"
    defined: bool = False
    token: str = ""
    productions: list[str] = field(default_factory=list)
    detail: str = ""

    @property
    def ok(self) -> bool:
        """A node name plus at least one production that builds it is what matters.

        The lexer token is reported for completeness: grammars that recognise the
        bracket through ``column_ref '[' expr ']'`` need no new token.
        """
        named = any(marker in self.node.upper() for marker in ELEMENT_ACCESS_NAMES)
        return self.defined and named and bool(self.productions)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""


def check(repo: Path) -> Finding:
    types_text = read(repo / ITEM_TYPES)
    lexer_text = read(repo / TOKENS)
    grammar_text = read(repo / GRAMMAR)
    if not types_text or not grammar_text:
        return Finding(detail=f"{repo} does not contain the parser sources")

    candidates = []
    for match in re.finditer(r"^\s*(T_[A-Z0-9_]*(?:ARRAY|ELEMENT)[A-Z0-9_]*)\s*=\s*(\d+)\s*,", types_text,
                             re.MULTILINE):
        candidates.append(match.group(1))
    new_nodes = [
        name for name in candidates
        if any(marker in name for marker in ELEMENT_ACCESS_NAMES)
    ]
    finding = Finding()
    # A node may be declared in the item-type header or introduced by the grammar
    # production alone (``malloc_non_terminal_node(..., T_OP_ARRAY_INDEX, ...)``); the
    # production is what proves the grammar can build it, so look there as well.
    used_nodes = sorted({
        match.group(1)
        for match in re.finditer(r"malloc_non_terminal_node\(\$\$, result->malloc_pool_,\s*(T_[A-Z0-9_]+)",
                                 grammar_text)
    })
    used_nodes += [name for name in candidates if name not in used_nodes]
    new_nodes = [
        name for name in used_nodes
        if any(marker in name.upper() for marker in ELEMENT_ACCESS_NAMES)
    ]
    if new_nodes:
        finding.node = new_nodes[0]
        finding.defined = True
        token_names = re.findall(r"^([A-Z][A-Z0-9_]*)\s", lexer_text, re.MULTILINE)
        for token in token_names:
            if token.endswith(("ARRAY_INDEX", "ARRAY_ELEMENT", "ARRAY_SUBSCRIPT")):
                finding.token = token
                break
        for match in re.finditer(r"malloc_non_terminal_node\(\$\$, result->malloc_pool_,\s*"
                                 rf"{re.escape(finding.node)}\b", grammar_text):
            line = grammar_text.count("\n", 0, match.start()) + 1
            start = grammar_text.rfind("\n", 0, max(0, match.start() - 120))
            snippet = grammar_text[start:match.start()].strip().replace("\n", " ")
            finding.productions.append(f"{line}: {snippet or finding.node}")
        finding.detail = (
            f"the grammar builds {finding.node} from a bracketed column reference; "
            f"{len(finding.productions)} production(s) use it"
        )
    else:
        bracket = re.search(r"^\|\s*'\[\'\s*expr_list\s*'\]'", grammar_text, re.MULTILINE)
        finding.detail = (
            "the grammar can only build an ARRAY literal for bracketed expressions "
            f"({'line ' + str(grammar_text.count(chr(10), 0, bracket.start()) + 1) if bracket else 'not found'})"
        )
        finding.detail += (
            "; the only ARRAY node the grammar builds from brackets is T_FUN_SYS_ARRAY "
            "(the ARRAY literal itself), so `col[i]` cannot become a distinct node"
        )
    return finding


def statements_probe(repo: Path, finding: Finding) -> list[dict]:
    """Per statement: is the bracketed column reference covered by a production?

    The check is static -- it never parses SQL -- so each statement is classified by
    whether the grammar has a production that accepts ``column_ref '[' ... ']'``.
    Without one, ``col[i]`` has nothing to reduce into and the server answers
    ``ERROR 1064`` (observed on the running 1.4.0 image).  Compile-time confirmation
    is the job of ``array_index_probe.cpp`` in this directory.
    """
    probes = [
        "select slots[2] from tc_read",
        "insert into tc_read(id, slots[2]) values (2, 9)",
        "update tc_read set slots[2] = 9 where id = 1",
        "delete from tc_read where slots[2] = 9",
        "select [10,20,30][2] from dual",
    ]
    return [
        {
            "statement": statement,
            "bracket_after_name": bool(re.search(r"\w\s*\[", statement)),
            "expected": (
                f"reduces into {finding.node} via the production at line "
                f"{finding.productions[0].split(':')[0]}" if finding.ok
                else "ERROR 1064: the grammar has no production for col[i]"
            ),
        }
        for statement in probes
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=os.environ.get("SEEKDB_REPO", ""))
    parser.add_argument("--json-out", default="")
    args = parser.parse_args()
    if not args.repo:
        print("GRAMMAR-CHECK: SKIP (set SEEKDB_REPO)")
        return 2
    repo = Path(args.repo).resolve()
    finding = check(repo)
    probes = statements_probe(repo, finding)
    print(f"repository: {repo}")
    print(f"element-access node: {finding.node if finding.defined else 'none'}")
    print(f"lexer token:         {finding.token or 'none'}")
    for production in finding.productions:
        print(f"production:          {production}")
    if finding.detail:
        print(f"detail:              {finding.detail}")
    for probe in probes:
        print(f"  {probe['statement']:<52} -> {probe['expected']}")
    verdict = "PASS" if finding.ok else "FAIL"
    print(f"GRAMMAR-CHECK: {verdict}")
    payload = {
        "ok": finding.ok,
        "node": finding.node,
        "defined": finding.defined,
        "token": finding.token,
        "productions": finding.productions,
        "detail": finding.detail,
        "probes": probes,
    }
    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    return 0 if finding.ok else 1


if __name__ == "__main__":
    sys.exit(main())
