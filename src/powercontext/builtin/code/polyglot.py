# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software distributed
# under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
# CONDITIONS OF ANY KIND, either express or implied. See the License for the
# specific language governing permissions and limitations under the License.

"""Resource-bounded Tree-sitter extraction for installed non-Python languages."""

from __future__ import annotations

import time
from pathlib import PurePosixPath

from powercontext.builtin.code.ecmascript import ECMAScriptCollector
from powercontext.builtin.code.errors import CodeError
from powercontext.builtin.code.golang import GoCollector
from powercontext.builtin.code.languages import parser_for

_COLLECTORS = {"javascript": ECMAScriptCollector, "typescript": ECMAScriptCollector, "go": GoCollector}


class PolyglotExtractor:
    def __init__(self, language: str, *, tsx: bool = False) -> None:
        self.language = language
        self.parser = parser_for(language, tsx=tsx)

    def extract(self, path: str, content: bytes, *, timeout: float = 5):
        deadline = time.monotonic() + timeout
        self.parser.reset()
        try:
            tree = self.parser.parse(content)
        except ValueError as error:
            raise CodeError("parse_timeout") from error
        if tree is None or time.monotonic() >= deadline:
            raise CodeError("parse_timeout")
        return _COLLECTORS[self.language](path, content, self.language, deadline).collect(tree.root_node)


def grammar_key(path: str, language: str) -> tuple[str, bool]:
    return language, language == "typescript" and PurePosixPath(path).suffix == ".tsx"
