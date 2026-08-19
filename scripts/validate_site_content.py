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

# ruff: noqa: TRY003 - validation errors report the exact configuration path
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FRONT_MATTER_PATTERN = re.compile(r"\A---[ \t]*\n(.*?)\n---[ \t]*(?:\n|\Z)", re.DOTALL)
HOME_PAGES = {
    Path("docs/en/index.md"): "en",
    Path("docs/zh/index.md"): "zh",
}
OVERVIEW_PAGES = {
    Path("docs/en/docs/index.md"): "en",
    Path("docs/zh/docs/index.md"): "zh",
}
TEMPLATE_PAGES = {
    "home.html": set(HOME_PAGES),
    "docs-overview.html": set(OVERVIEW_PAGES),
}


class SiteContentError(ValueError):
    """Raised when configured site content does not match the template contract."""


def _read_front_matter(relative_path: Path) -> Mapping[str, Any]:
    source_path = PROJECT_ROOT / relative_path
    source = source_path.read_text(encoding="utf-8")
    match = FRONT_MATTER_PATTERN.match(source)
    if match is None:
        raise SiteContentError(f"{relative_path}: missing YAML front matter")

    try:
        metadata = yaml.safe_load(match.group(1))
    except yaml.YAMLError as error:
        raise SiteContentError(f"{relative_path}: invalid YAML: {error}") from error

    if not isinstance(metadata, Mapping):
        raise SiteContentError(f"{relative_path}: front matter must be a mapping")
    return metadata


def _require_mapping(parent: Mapping[str, Any], key: str, location: str) -> Mapping[str, Any]:
    value = parent.get(key)
    if not isinstance(value, Mapping):
        raise SiteContentError(f"{location}.{key}: expected a mapping")
    return value


def _require_list(parent: Mapping[str, Any], key: str, location: str) -> list[Any]:
    value = parent.get(key)
    if not isinstance(value, list) or not value:
        raise SiteContentError(f"{location}.{key}: expected a non-empty list")
    return value


def _require_string(parent: Mapping[str, Any], key: str, location: str) -> str:
    value = parent.get(key)
    if not isinstance(value, str) or not value.strip():
        raise SiteContentError(f"{location}.{key}: expected a non-empty string")
    return value


def _require_string_list(
    parent: Mapping[str, Any],
    key: str,
    location: str,
    *,
    expected_length: int | None = None,
) -> list[str]:
    values = _require_list(parent, key, location)
    if not all(isinstance(value, str) and value.strip() for value in values):
        raise SiteContentError(f"{location}.{key}: expected non-empty strings")
    if expected_length is not None and len(values) != expected_length:
        raise SiteContentError(f"{location}.{key}: expected {expected_length} entries")
    return values


def _validate_href(href: str, locale: str, location: str, *, allow_external: bool = False) -> None:
    if href.startswith(("https://", "http://")):
        if allow_external:
            return
        raise SiteContentError(f"{location}: external links are not allowed")
    if href.startswith("/") or href.endswith(".md") or not href.startswith(f"{locale}/"):
        raise SiteContentError(f"{location}: expected a locale-relative output path beginning with '{locale}/'")


def _validate_action(action: Any, locale: str, location: str) -> str:
    if not isinstance(action, Mapping):
        raise SiteContentError(f"{location}: expected a mapping")
    _require_string(action, "label", location)
    href = _require_string(action, "href", location)
    _validate_href(href, locale, f"{location}.href", allow_external=True)
    return _require_string(action, "kind", location)


def _validate_home(metadata: Mapping[str, Any], locale: str, source: Path) -> None:
    if metadata.get("template") != "home.html":
        raise SiteContentError(f"{source}: expected template 'home.html'")
    location = f"{source}:home"
    home = _require_mapping(metadata, "home", str(source))

    hero = _require_mapping(home, "hero", location)
    _require_string(hero, "label", f"{location}.hero")
    _require_string_list(hero, "title", f"{location}.hero", expected_length=2)
    _require_string(hero, "lead", f"{location}.hero")
    _require_string(hero, "note", f"{location}.hero")
    actions = _require_list(hero, "actions", f"{location}.hero")
    action_kinds = {
        _validate_action(action, locale, f"{location}.hero.actions[{index}]") for index, action in enumerate(actions)
    }
    if action_kinds != {"primary", "secondary"} or len(actions) != 2:
        raise SiteContentError(f"{location}.hero.actions: expected one primary and one secondary action")

    continuity = _require_mapping(home, "continuity", location)
    _require_string(continuity, "label", f"{location}.continuity")
    _require_string(continuity, "title", f"{location}.continuity")
    _require_string(continuity, "lead", f"{location}.continuity")
    steps = _require_list(continuity, "steps", f"{location}.continuity")
    if len(steps) > 9:
        raise SiteContentError(f"{location}.continuity.steps: expected at most 9 entries")
    for index, step in enumerate(steps):
        step_location = f"{location}.continuity.steps[{index}]"
        if not isinstance(step, Mapping):
            raise SiteContentError(f"{step_location}: expected a mapping")
        _require_string(step, "title", step_location)
        _require_string(step, "description", step_location)

    ownership = _require_mapping(home, "ownership", location)
    _require_string(ownership, "label", f"{location}.ownership")
    _require_string_list(ownership, "title", f"{location}.ownership", expected_length=2)
    _require_string(ownership, "lead", f"{location}.ownership")
    _require_string(ownership, "command", f"{location}.ownership")
    for action_name in ("primary_action", "secondary_action"):
        action = _require_mapping(ownership, action_name, f"{location}.ownership")
        _require_string(action, "label", f"{location}.ownership.{action_name}")
        href = _require_string(action, "href", f"{location}.ownership.{action_name}")
        _validate_href(href, locale, f"{location}.ownership.{action_name}.href")


def _validate_overview(metadata: Mapping[str, Any], locale: str, source: Path) -> None:
    if metadata.get("template") != "docs-overview.html":
        raise SiteContentError(f"{source}: expected template 'docs-overview.html'")
    location = f"{source}:overview"
    overview = _require_mapping(metadata, "overview", str(source))
    _require_string(overview, "intro", location)
    sections = _require_list(overview, "sections", location)
    hrefs: set[str] = set()

    for section_index, section in enumerate(sections):
        section_location = f"{location}.sections[{section_index}]"
        if not isinstance(section, Mapping):
            raise SiteContentError(f"{section_location}: expected a mapping")
        _require_string(section, "title", section_location)
        _require_string(section, "description", section_location)
        cards = _require_list(section, "cards", section_location)
        for card_index, card in enumerate(cards):
            card_location = f"{section_location}.cards[{card_index}]"
            if not isinstance(card, Mapping):
                raise SiteContentError(f"{card_location}: expected a mapping")
            _require_string(card, "title", card_location)
            _require_string(card, "description", card_location)
            href = _require_string(card, "href", card_location)
            _validate_href(href, locale, f"{card_location}.href")
            if href in hrefs:
                raise SiteContentError(f"{card_location}.href: duplicate card target '{href}'")
            hrefs.add(href)


def _validate_template_assignments() -> None:
    discovered = {template: set() for template in TEMPLATE_PAGES}
    for source_path in (PROJECT_ROOT / "docs").rglob("*.md"):
        relative_path = source_path.relative_to(PROJECT_ROOT)
        source = source_path.read_text(encoding="utf-8")
        if FRONT_MATTER_PATTERN.match(source) is None:
            continue
        metadata = _read_front_matter(relative_path)
        template = metadata.get("template")
        if template in discovered:
            discovered[template].add(relative_path)

    for template, expected_pages in TEMPLATE_PAGES.items():
        if discovered[template] != expected_pages:
            expected = ", ".join(str(path) for path in sorted(expected_pages))
            actual = ", ".join(str(path) for path in sorted(discovered[template])) or "none"
            raise SiteContentError(f"{template}: expected pages [{expected}], found [{actual}]")


def validate_site_content() -> None:
    errors: list[str] = []
    try:
        _validate_template_assignments()
    except (OSError, SiteContentError) as error:
        errors.append(str(error))
    for source, locale in HOME_PAGES.items():
        try:
            _validate_home(_read_front_matter(source), locale, source)
        except (OSError, SiteContentError) as error:
            errors.append(str(error))
    for source, locale in OVERVIEW_PAGES.items():
        try:
            _validate_overview(_read_front_matter(source), locale, source)
        except (OSError, SiteContentError) as error:
            errors.append(str(error))

    if errors:
        raise SystemExit("Site content configuration is invalid:\n" + "\n".join(f"- {error}" for error in errors))


if __name__ == "__main__":
    validate_site_content()
