# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software distributed
# under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
# CONDITIONS OF ANY KIND, either express or implied. See the License for the
# specific language governing permissions and limitations under the License.

"""Installed grammar registry and language conventions for repository evidence."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from powercontext.builtin.code.errors import CodeError

if TYPE_CHECKING:
    from tree_sitter import Parser


@dataclass(frozen=True)
class LanguageSpec:
    module: str
    grammar_version: str
    extractor_version: str
    extensions: tuple[str, ...]

    @property
    def build(self) -> str:
        return f"tree-sitter-0.26.0/{self.module}-{self.grammar_version}/{self.extractor_version}"


LANGUAGES = {
    "python": LanguageSpec("tree_sitter_python", "0.25.0", "python-5", (".py", ".pyi")),
    "javascript": LanguageSpec("tree_sitter_javascript", "0.25.0", "ecmascript-3", (".js", ".jsx", ".mjs", ".cjs")),
    "typescript": LanguageSpec("tree_sitter_typescript", "0.23.2", "ecmascript-3", (".ts", ".tsx", ".mts", ".cts")),
    "go": LanguageSpec("tree_sitter_go", "0.25.0", "go-2", (".go",)),
}
SUPPORTED_LANGUAGES = tuple(LANGUAGES)
LANGUAGE_LIMITATIONS = {
    "python": (),
    "javascript": ("esm_relative_imports_only", "javascript_types_and_inheritance_not_resolved"),
    "typescript": ("esm_relative_imports_only", "typescript_types_and_inheritance_not_resolved"),
    "go": ("go_current_module_only", "go_receiver_types_not_resolved", "go_build_constraints_not_evaluated"),
}
_EXTENSIONS = {extension: language for language, spec in LANGUAGES.items() for extension in spec.extensions}


def language_for_path(path: str) -> str:
    return _EXTENSIONS.get(PurePosixPath(path).suffix, "text")


def parser_builds() -> dict[str, str]:
    return {**{language: spec.build for language, spec in LANGUAGES.items()}, "text": "text-2"}


def parser_for(language: str, *, tsx: bool = False) -> Parser:
    """Load only a pinned, installed grammar; never download or execute repository code."""
    try:
        from tree_sitter import Language, Parser

        module = import_module(LANGUAGES[language].module)
        factory = "language_tsx" if tsx else "language_typescript" if language == "typescript" else "language"
        return Parser(Language(getattr(module, factory)()))
    except (ImportError, AttributeError, ValueError) as error:
        raise CodeError("code_parser_unavailable") from error


def is_test_path(path: str) -> bool:
    file = PurePosixPath(path)
    language = language_for_path(path)
    if language == "python":
        return file.suffix == ".py" and (file.name.startswith("test_") or file.name.endswith("_test.py"))
    if language == "go":
        return file.name.endswith("_test.go")
    if language in {"javascript", "typescript"}:
        return "__tests__" in file.parts or file.stem.endswith((".test", ".spec"))
    return False


def test_stem(path: str) -> str:
    file = PurePosixPath(path)
    stem = file.stem
    if language_for_path(path) == "python" and stem.startswith("test_"):
        return stem[5:]
    for suffix in ("_test", ".test", ".spec"):
        if stem.endswith(suffix):
            return stem[: -len(suffix)]
    return stem
