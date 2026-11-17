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

"""Exercise scope navigation with the real HTMX and Tom Select scripts.

Requires the notebooks extra and an installed Playwright Chromium browser.
POWERCONTEXT_TEST_BROWSER_CHANNEL can select an installed Chrome or Edge instead.
"""

import os
import time

import pytest
from fastapi.testclient import TestClient

from tests.test_dashboard import create_scope
from tests.test_dashboard import dashboard as dashboard


@pytest.mark.parametrize("selection", ["mouse", "keyboard"])
def test_scope_navigation_keeps_picker_closed(dashboard: TestClient, selection: str) -> None:
    playwright = pytest.importorskip("playwright.sync_api")
    first = create_scope(dashboard, "Browser scope Alpha")["scope_id"]
    second = create_scope(dashboard, "Browser scope Beta")["scope_id"]

    with playwright.sync_playwright() as runtime:
        try:
            browser = runtime.chromium.launch(channel=os.environ.get("POWERCONTEXT_TEST_BROWSER_CHANNEL"))
        except playwright.Error as error:
            if "Executable doesn't exist" in str(error):
                pytest.skip("Install Playwright Chromium or set POWERCONTEXT_TEST_BROWSER_CHANNEL")
            raise
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(error))

        def serve(route):
            request = route.request
            if "/dashboard/scope-options?" in request.url:
                # Exercise options arriving after workspace replacement, too.
                time.sleep(0.3)
            response = dashboard.request(request.method, request.url, content=request.post_data_buffer)
            headers = {k: v for k, v in response.headers.items() if k not in {"content-length", "content-encoding"}}
            route.fulfill(status=response.status_code, headers=headers, body=response.content)

        page.route("http://testserver/**", serve)
        page.goto(f"http://testserver/dashboard/home?scope={first}")
        page.locator(".scope-picker .ts-control").click()
        search = page.locator(".scope-picker .dropdown-input")
        search.fill("Browser scope Beta")
        option = page.locator(f'.scope-picker .option[data-value="{second}"]')
        option.wait_for(state="visible")
        if selection == "mouse":
            option.click()
        else:
            search.press("ArrowDown")
            search.press("Enter")
        page.wait_for_url(f"**scope={second}")
        page.wait_for_function("document.querySelector('#scope').tomselect.loading === 0")
        assert page.evaluate("document.querySelector('#scope').tomselect.isOpen") is False
        assert page.locator("#main").evaluate("element => element === document.activeElement")

        # The replacement picker must still open, search, and close normally.
        page.locator(".scope-picker .ts-control").click()
        search.fill("Browser scope Alpha")
        page.locator(f'.scope-picker .option[data-value="{first}"]').wait_for(state="visible")
        search.press("Escape")
        assert page.evaluate("document.querySelector('#scope').tomselect.isOpen") is False
        assert not errors
        browser.close()
