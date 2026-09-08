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

"""Capture content-redacted desktop and narrow Topic dashboard evidence."""

# ruff: noqa: TRY003

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.sync_api import Page  # ty: ignore[unresolved-import]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--artifact-ref", required=True)
    parser.add_argument("--source-ref", required=True)
    parser.add_argument("--desktop", type=Path, required=True)
    parser.add_argument("--narrow", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    return parser.parse_args()


def _open_exact_topic(page: Page, *, base_url: str, token: str, artifact_ref: str, source_ref: str) -> None:
    page.goto(f"{base_url}/topics", wait_until="networkidle")
    page.locator("#token").fill(token)
    page.locator("#auth-form button[type=submit]").click()
    page.locator("#topics-list .topics-list-item").first.wait_for(state="visible")
    page.locator("#topics-list .topics-list-item").first.click()
    page.locator("#topics-detail-ref").filter(has_text=artifact_ref).wait_for(state="visible")
    page.locator("#topics-source-refs").filter(has_text=source_ref).wait_for(state="visible")
    # Keep exact identity and SourceRef visible while excluding generated content.
    page.locator("#topics-detail-title").evaluate("element => element.textContent = '[redacted generated title]' ")
    page.locator("#topics-detail-summary").evaluate("element => element.textContent = '[redacted generated summary]' ")
    page.locator("#topics-detail-body").evaluate("element => element.textContent = '[redacted generated detail]' ")
    page.locator("#topics-list .topics-list-item").evaluate_all(
        "elements => elements.forEach(element => { const text = element.querySelectorAll('strong, p'); "
        "text.forEach(node => node.textContent = '[redacted generated content]'); })"
    )


def main() -> int:
    from playwright.sync_api import sync_playwright  # ty: ignore[unresolved-import]

    args = _parse_args()
    token = os.environ.get("POWERCONTEXT_R8_BROWSER_TOKEN")
    if not token:
        raise RuntimeError("POWERCONTEXT_R8_BROWSER_TOKEN is required")
    args.desktop.parent.mkdir(parents=True, exist_ok=True)
    observations: list[dict[str, object]] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            executable_path=os.environ.get("POWERCONTEXT_R8_BROWSER_EXECUTABLE"),
        )
        try:
            for name, viewport, path in (
                ("desktop", {"width": 1440, "height": 1000}, args.desktop),
                ("narrow", {"width": 390, "height": 844}, args.narrow),
            ):
                page = browser.new_page(viewport=viewport)
                _open_exact_topic(
                    page,
                    base_url=args.base_url,
                    token=token,
                    artifact_ref=args.artifact_ref,
                    source_ref=args.source_ref,
                )
                page.screenshot(path=str(path), full_page=True)
                observations.append({
                    "viewport": name,
                    "width": viewport["width"],
                    "height": viewport["height"],
                    "artifact_ref_visible": args.artifact_ref,
                    "source_ref_visible": args.source_ref,
                    "generated_content_redacted": True,
                })
                page.close()
        finally:
            browser.close()
    args.audit.write_text(
        f"{json.dumps({'status': 'PASS', 'observations': observations}, indent=2, sort_keys=True)}\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
