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

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_STATIC = _ROOT / "src" / "powercontext" / "server" / "static"
_TEMPLATES = _ROOT / "src" / "powercontext" / "server" / "templates"
_ALLOWED_LATIN = {"Claude", "Code", "Codex", "EN", "HTTP", "Markdown", "OceanBase", "PowerContext"}
_CJK = re.compile(r"[\u4e00-\u9fff]")
_LATIN_WORD = re.compile(r"[A-Za-z]{2,}")
_PLACEHOLDER = re.compile(r"\{[A-Za-z]+\}")
_I18N_ATTR = re.compile(r'data-i18n(?:-aria-label|-title|-placeholder)?="([^"]+)"')
_CATALOGS = (
    (
        _STATIC / "dashboard.js",
        (
            _TEMPLATES / "pages" / "dashboard.html",
            _TEMPLATES / "components" / "header.html",
            _TEMPLATES / "components" / "footer.html",
            _TEMPLATES / "components" / "login.html",
            _TEMPLATES / "components" / "status.html",
            _TEMPLATES / "components" / "activity_heatmap.html",
            _TEMPLATES / "components" / "recall_trend.html",
        ),
    ),
    (
        _STATIC / "handoff-report.js",
        (
            _TEMPLATES / "pages" / "handoff_report.html",
            _TEMPLATES / "components" / "header.html",
            _TEMPLATES / "components" / "footer.html",
            _TEMPLATES / "components" / "login.html",
            _TEMPLATES / "components" / "status.html",
        ),
    ),
    (
        _STATIC / "review.js",
        (
            _TEMPLATES / "pages" / "review.html",
            _TEMPLATES / "components" / "header.html",
            _TEMPLATES / "components" / "footer.html",
            _TEMPLATES / "components" / "login.html",
            _TEMPLATES / "components" / "status.html",
        ),
    ),
    (
        _STATIC / "skills.js",
        (
            _TEMPLATES / "pages" / "skills.html",
            _TEMPLATES / "components" / "header.html",
            _TEMPLATES / "components" / "footer.html",
            _TEMPLATES / "components" / "login.html",
            _TEMPLATES / "components" / "status.html",
        ),
    ),
)


@pytest.mark.parametrize(("script_path", "template_paths"), _CATALOGS)
def test_static_locale_catalogs_are_complete_and_not_mixed(script_path: Path, template_paths: tuple[Path, ...]) -> None:
    catalog = _load_translations(script_path)
    assert set(catalog) == {"en", "zh"}
    assert set(catalog["en"]) == set(catalog["zh"])

    template_keys = {
        key
        for path in template_paths
        for key in _I18N_ATTR.findall(path.read_text(encoding="utf-8"))
        if "{{" not in key
    }
    missing = sorted(template_keys - set(catalog["en"]))
    assert missing == [], f"{script_path.name} is missing translations for {missing}"

    leaks = [
        f"{key}: {catalog['zh'][key]}"
        for key, english in catalog["en"].items()
        if _latin_leak(english, catalog["zh"][key])
    ]
    assert leaks == [], f"{script_path.name} still mixes English into Chinese copy: {leaks}"


def _load_translations(path: Path) -> dict[str, dict[str, str]]:
    source = path.read_text(encoding="utf-8")
    start = source.index("const translations = ") + len("const translations = ")
    depth = 0
    end = None
    for index, char in enumerate(source[start:], start):
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                end = index + 1
                break
    assert end is not None
    return {locale: _parse_entries(block) for locale, block in _locale_blocks(source[start:end]).items()}


def _locale_blocks(object_source: str) -> dict[str, str]:
    blocks: dict[str, str] = {}
    for match in re.finditer(r"\b(en|zh):\s*\{", object_source):
        start = match.end() - 1
        depth = 0
        for index, char in enumerate(object_source[start:], start):
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    blocks[match.group(1)] = object_source[start : index + 1]
                    break
    return blocks


def _parse_entries(block: str) -> dict[str, str]:
    entries: dict[str, str] = {}
    for match in re.finditer(r'(?:([A-Za-z_][\w-]*)|"([^"]+)"):\s*"((?:\\.|[^"\\])*)"', block):
        key = match.group(1) or match.group(2)
        entries[key] = json.loads(f'"{match.group(3)}"')
    return entries


def _latin_leak(english: str, chinese: str) -> str | None:
    stripped = _PLACEHOLDER.sub("", chinese)
    words = {word for word in _LATIN_WORD.findall(stripped) if word not in _ALLOWED_LATIN}
    if _CJK.search(chinese):
        return ", ".join(sorted(words)) if words else None
    if words and chinese == english:
        return "untranslated"
    return ", ".join(sorted(words)) if words else None
