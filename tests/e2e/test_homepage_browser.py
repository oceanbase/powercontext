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

"""Browser regressions for the built homepage.

Run after ``make docs-test`` with Playwright's Chromium installed:
``uv run --group notebooks pytest tests/e2e/test_homepage_browser.py -q``.
Set POWERCONTEXT_WEBSITE_URL to exercise an already running preview instead.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest

playwright = pytest.importorskip("playwright.sync_api")


@pytest.fixture(scope="module")
def homepage_url() -> Iterator[str]:
    if url := os.environ.get("POWERCONTEXT_WEBSITE_URL"):
        yield url.rstrip("/")
        return
    output = Path(__file__).resolve().parents[2] / "website/out"
    if not (output / "zh/index.html").is_file():
        pytest.skip("Homepage browser tests require a successful make docs-test build.")
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=str(output)))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.fixture(scope="module")
def browser():
    with playwright.sync_playwright() as driver:
        browser = driver.chromium.launch()
        yield browser
        browser.close()


@pytest.fixture
def page(browser):
    with browser.new_context(viewport={"width": 1440, "height": 1000}, reduced_motion="no-preference") as context:
        yield context.new_page()


def return_from_docs(page, homepage_url: str) -> None:
    page.goto(f"{homepage_url}/zh/docs/")
    page.evaluate("window.homepageNavigationMarker = true")
    page.locator('a[href="/zh/"]:visible').first.click()
    playwright.expect(page.locator(".pc-demo")).to_be_visible()
    assert page.evaluate("window.homepageNavigationMarker"), "Exercise client navigation, not a document reload"


@pytest.mark.parametrize("navigation", ["direct", "client"])
def test_hero_plays_on_homepage_entry(page, homepage_url: str, navigation: str) -> None:
    if navigation == "client":
        return_from_docs(page, homepage_url)
    else:
        page.goto(f"{homepage_url}/zh/")
    page.locator(".pc-demo").scroll_into_view_if_needed()
    cursor = page.locator(".pc-cursor-tag").first
    playwright.expect(cursor).to_be_visible()
    position = cursor.bounding_box()
    assert position
    page.wait_for_function(
        """before => {
            const box = document.querySelector('.pc-cursor-tag').getBoundingClientRect();
            return Math.abs(box.x - before.x) > 2 || Math.abs(box.y - before.y) > 2;
        }""",
        arg=position,
        timeout=15000,
    )


@pytest.mark.parametrize("navigation", ["direct", "client"])
def test_reduced_motion_keeps_the_hero_static(page, homepage_url: str, navigation: str) -> None:
    page.emulate_media(reduced_motion="reduce")
    if navigation == "client":
        return_from_docs(page, homepage_url)
    else:
        page.goto(f"{homepage_url}/zh/")
    playwright.expect(page.locator(".pc-cursors")).to_be_hidden()
    playwright.expect(page.locator('[data-session="next"] .pc-action-done')).to_be_visible()


def expect_focus_visible(page) -> None:
    page.wait_for_function(
        """() => {
            const link = document.activeElement;
            const box = link.getBoundingClientRect();
            const hit = document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2);
            return box.top >= 0 && box.bottom <= innerHeight && link.contains(hit);
        }""",
        timeout=3000,
    )


@pytest.mark.parametrize("viewport", [{"width": 390, "height": 844}, {"width": 1440, "height": 1000}])
def test_footer_focus_is_visible_after_tabbing_from_installation(
    page, homepage_url: str, viewport: dict[str, int]
) -> None:
    page.set_viewport_size(viewport)
    page.goto(f"{homepage_url}/zh/")
    copy = page.locator(".pc-copy").last
    copy.scroll_into_view_if_needed()
    copy.focus()
    page.keyboard.press("Tab")
    logo = page.locator("main.pc-home > footer a").first
    playwright.expect(logo).to_be_focused()
    expect_focus_visible(page)
    page.keyboard.press("Shift+Tab")
    playwright.expect(copy).to_be_focused()
    expect_focus_visible(page)


@pytest.mark.parametrize("width", [390, 1440])
def test_visible_repeated_testimonial_opens_its_link(page, homepage_url: str, width: int) -> None:
    page.set_viewport_size({"width": width, "height": 844})
    page.goto(f"{homepage_url}/zh/")
    page.locator(".pc-quotes-row").scroll_into_view_if_needed()
    # Seek near the end of the real CSS animation instead of waiting an entire loop.
    page.locator(".pc-quotes-track").evaluate(
        """track => {
            const animation = track.getAnimations()[0];
            animation.currentTime = Number(animation.effect.getTiming().duration) * 0.93;
        }"""
    )
    page.locator(".pc-quotes-toggle").click()
    link = page.locator(".pc-quotes-set[data-repeat] a").first
    link.scroll_into_view_if_needed()
    box = link.bounding_box()
    assert box and 0 < box["x"] + box["width"] / 2 < width
    assert 0 < box["y"] + box["height"] / 2 < 844
    href = link.get_attribute("href")
    # Confirm the click opens the advertised destination without depending on X's network access.
    page.context.route("https://x.com/**", lambda route: route.fulfill(body="Author profile"))
    with page.expect_popup(timeout=3000) as opened:
        link.click(timeout=3000)
    popup = opened.value
    popup.wait_for_load_state()
    assert popup.url == href
    popup.close()
    original = page.locator(".pc-quotes-set:not([data-repeat]) a").last
    original.focus()
    page.keyboard.press("Tab")
    assert not page.locator(".pc-quotes-set[data-repeat] a:focus").count()
