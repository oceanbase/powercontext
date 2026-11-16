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

"""Real browser navigation must not reuse another knowledge base's requests or content."""

import asyncio
import os
import shutil
from pathlib import Path

import pytest

from tests.e2e.plugin_server import ADMIN_AUTHORIZATION_VALUE, running_server, wait_for
from tests.e2e.test_knowledge_base import FIXTURE_EMBEDDING, PLUGIN, P


@pytest.mark.parametrize("delayed", ["documents", "imports"])
def test_dashboard_discards_previous_library_loads_and_uses_framework_artifact_api(tmp_path, delayed):
    playwright = pytest.importorskip("playwright.async_api")
    plugins = tmp_path / "plugins"
    shutil.copytree(PLUGIN, plugins / "knowledge-base")
    entry = plugins / "knowledge-base" / "plugin.py"
    entry.write_text(entry.read_text() + FIXTURE_EMBEDDING)

    async def scenario():
        async with (
            running_server(
                tmp_path,
                plugins,
                extra_env={
                    "POWERCONTEXT_KB_DATA_DIR": str(tmp_path / "data"),
                    "POWERCONTEXT_SERVER_DASHBOARD_ENABLED": "true",
                },
            ) as client,
            playwright.async_playwright() as pw,
        ):
            executable = os.environ.get("POWERCONTEXT_TEST_CHROMIUM", pw.chromium.executable_path)
            if not await asyncio.to_thread(Path(executable).exists):
                pytest.skip("Install Chromium with playwright install chromium, or set POWERCONTEXT_TEST_CHROMIUM")

            async def call(method, path, **kwargs):
                response = await client.request(method, P + path, **kwargs)
                assert response.is_success, response.text
                return response.json()

            space = (await call("GET", "/me/default-space"))["space_scope_id"]
            libraries = []
            for title, paths in [("A", ["a.md", "a-more.md"]), ("B", ["b.md"])]:
                kb = await call(
                    "POST", "/knowledge-bases", json={"title": title, "space_scope_id": space, "idempotency_key": title}
                )
                libraries.append(kb["scope_id"])
                for path in paths:
                    base = "/scopes/" + kb["scope_id"]
                    receipt = await call(
                        "POST",
                        base + "/uploads",
                        params={"relative_path": path, "idempotency_key": path},
                        content=("# " + path + "\n\nBody " + title).encode(),
                    )
                    await wait_for(
                        lambda base=base, receipt=receipt: call("GET", base + "/imports/" + receipt["request_id"]),
                        lambda r: r["state"] == "completed",
                        log=tmp_path / "server.log",
                    )
            a, b = libraries
            browser = await pw.chromium.launch(executable_path=executable)
            context = await browser.new_context()
            origin = str(client.base_url).rstrip("/")
            await context.add_cookies([
                {"name": "powercontext_dashboard_token", "value": ADMIN_AUTHORIZATION_VALUE, "url": origin}
            ])
            page = await context.new_page()
            await page.goto(origin + "/dashboard/plugins/knowledge-base")
            frame = page.frame_locator("iframe")
            await frame.get_by_role("button", name="A", exact=True).wait_for()
            wrong_requests = []
            a_requests = (await call("GET", "/scopes/" + a + "/documents"))["requests"]
            a_ids = {r["request_id"] for r in a_requests}
            page.on(
                "request",
                lambda r: (
                    wrong_requests.append(r.url)
                    if "/scopes/" + b + "/imports/" in r.url and r.url.rsplit("/", 1)[-1] in a_ids
                    else None
                ),
            )
            await page.evaluate(
                """({scope, delayed}) => {
              const fetch = window.fetch;
              window.kbDelay = {held: false, returned: false};
              window.fetch = async (...args) => {
                const response = await fetch(...args);
                const path = new URL(String(args[0])).pathname;
                if (!window.kbDelay.held && path.includes('/scopes/' + scope + '/' + delayed)) {
                  window.kbDelay.held = true;
                  await new Promise(resolve => { window.kbDelay.release = resolve; });
                  window.kbDelay.returned = true;
                }
                return response;
              };
            }""",
                {"scope": a, "delayed": delayed},
            )
            await frame.get_by_role("button", name="A", exact=True).click()
            await page.wait_for_function("window.kbDelay.held")
            await frame.get_by_role("button", name="B", exact=True).click()
            await playwright.expect(frame.locator("#documents")).to_contain_text("b.md")
            await playwright.expect(frame.locator("#requests")).to_contain_text("completed")
            await page.evaluate("window.kbDelay.release()")
            await page.wait_for_function("window.kbDelay.returned")
            # Let the already-delivered bridge reply and its DOM callbacks finish.
            await page.wait_for_timeout(300)
            assert not wrong_requests
            await playwright.expect(frame.locator("#library-title")).to_have_text("B")
            await playwright.expect(frame.locator("#documents")).not_to_contain_text("a.md")
            await playwright.expect(frame.locator("#notice")).not_to_contain_text("404")
            status_text = await frame.locator("#requests").inner_text()
            assert all(r["document_id"][:10] not in status_text for r in a_requests)

            # The page must use the upstream family-bound Artifact bridge for edits.
            await frame.locator("#documents").get_by_role("button", name="浏览", exact=True).click()
            await frame.locator("#chunks").get_by_role("button", name="编辑", exact=True).first.click()
            await frame.locator("#editor").wait_for(state="visible")
            await frame.locator("#chunk-text").fill("Browser correction")
            async with page.expect_response(
                lambda r: "/artifacts/" + b + "/" in r.url and r.request.method == "PUT"
            ) as saved:
                await frame.locator("#save-chunk").click()
            assert (await saved.value).status == 200
            await frame.locator("#editor").wait_for(state="hidden")
            assert (await call("GET", "/scopes/" + b + "/documents"))["items"][0]["ref"]["revision"] == 2

            # Exercise the unavailable-default response contract; actual revocation is covered
            # by the multi-identity HTTP test (Dashboard currently uses a static identity).
            default = await call("GET", "/me/default-space")
            await page.route(
                "**/api/me/default-space",
                lambda route: route.fulfill(json={**default, "space_scope_id": "revoked", "available": False}),
            )
            await page.reload()
            await playwright.expect(frame.locator("#notice")).to_contain_text("默认空间不可访问")
            await playwright.expect(frame.locator("#spaces")).to_have_value("")
            await frame.locator("#spaces").select_option(space)
            await frame.get_by_role("button", name="B", exact=True).click()
            await playwright.expect(frame.locator("#documents")).to_contain_text("b.md")
            await browser.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("selection", ["same", "another"])
def test_dashboard_keeps_saved_settings_when_reselecting_a_library(tmp_path, selection):
    playwright = pytest.importorskip("playwright.async_api")
    plugins = tmp_path / "plugins"
    shutil.copytree(PLUGIN, plugins / "knowledge-base")
    entry = plugins / "knowledge-base" / "plugin.py"
    entry.write_text(entry.read_text() + FIXTURE_EMBEDDING)

    async def scenario():
        async with (
            running_server(
                tmp_path,
                plugins,
                extra_env={
                    "POWERCONTEXT_KB_DATA_DIR": str(tmp_path / "data"),
                    "POWERCONTEXT_SERVER_DASHBOARD_ENABLED": "true",
                },
            ) as client,
            playwright.async_playwright() as pw,
        ):
            executable = os.environ.get("POWERCONTEXT_TEST_CHROMIUM", pw.chromium.executable_path)
            if not await asyncio.to_thread(Path(executable).exists):
                pytest.skip("Install Chromium with playwright install chromium, or set POWERCONTEXT_TEST_CHROMIUM")

            async def call(method, path, **kwargs):
                response = await client.request(method, P + path, **kwargs)
                assert response.is_success, response.text
                return response.json()

            space = (await call("GET", "/me/default-space"))["space_scope_id"]
            for title in ("Settings A", "Settings B"):
                await call(
                    "POST", "/knowledge-bases", json={"title": title, "space_scope_id": space, "idempotency_key": title}
                )
            browser = await pw.chromium.launch(executable_path=executable)
            context = await browser.new_context()
            origin = str(client.base_url).rstrip("/")
            await context.add_cookies([
                {"name": "powercontext_dashboard_token", "value": ADMIN_AUTHORIZATION_VALUE, "url": origin}
            ])
            page = await context.new_page()
            await page.goto(origin + "/dashboard/plugins/knowledge-base")
            frame = page.frame_locator("iframe")

            async def select(title):
                await frame.get_by_role("button", name=title, exact=True).click()
                await playwright.expect(frame.locator("#library")).to_be_visible()
                await playwright.expect(frame.locator("#library-title")).to_have_text(title)

            async def configure(expected, maximum):
                await frame.locator("#settings").click()
                await playwright.expect(frame.locator("#question-input")).to_have_value(str(expected))
                await frame.locator("#question-input").fill(str(maximum))
                await frame.locator("#question-confirm").click()
                await playwright.expect(frame.locator("#question-label")).to_have_text("重叠字符数")
                await frame.locator("#question-input").fill("40")
                async with page.expect_response(
                    lambda r: r.request.method == "PATCH" and r.url.endswith("/settings")
                ) as saved:
                    await frame.locator("#question-confirm").click()
                response = await saved.value
                assert response.status == 200, await response.text()
                await playwright.expect(frame.locator("#notice")).to_contain_text("配置已保存")

            await select("Settings A")
            await configure(360, 300)
            if selection == "another":
                await select("Settings B")
            await select("Settings A")
            await configure(300, 280)
            rows = (await call("GET", "/knowledge-bases", params={"space_scope_id": space}))["items"]
            saved = next(row for row in rows if row["value"]["title"] == "Settings A")
            assert saved["version"] == 3
            assert saved["value"]["chunking"] == {"max_chars": 280, "overlap": 40}
            other = next(row for row in rows if row["value"]["title"] == "Settings B")
            assert other["version"] == 1
            assert other["value"]["chunking"]["max_chars"] == 360
            await browser.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("delayed", ["/me/default-space", "/knowledge-bases"])
def test_dashboard_invalidates_library_controls_when_switching_spaces(tmp_path, delayed):
    playwright = pytest.importorskip("playwright.async_api")
    plugins = tmp_path / "plugins"
    shutil.copytree(PLUGIN, plugins / "knowledge-base")
    entry = plugins / "knowledge-base" / "plugin.py"
    entry.write_text(entry.read_text() + FIXTURE_EMBEDDING)

    async def scenario():
        async with (
            running_server(
                tmp_path,
                plugins,
                extra_env={
                    "POWERCONTEXT_KB_DATA_DIR": str(tmp_path / "data"),
                    "POWERCONTEXT_SERVER_DASHBOARD_ENABLED": "true",
                },
            ) as client,
            playwright.async_playwright() as pw,
        ):
            executable = os.environ.get("POWERCONTEXT_TEST_CHROMIUM", pw.chromium.executable_path)
            if not await asyncio.to_thread(Path(executable).exists):
                pytest.skip("Install Chromium with playwright install chromium, or set POWERCONTEXT_TEST_CHROMIUM")

            async def call(method, path, **kwargs):
                response = await client.request(method, P + path, **kwargs)
                assert response.is_success, response.text
                return response.json()

            a = (await call("GET", "/me/default-space"))["space_scope_id"]
            b = (await call("POST", "/spaces", json={"title": "Space B", "idempotency_key": "B"}))["space_scope_id"]
            libraries = []
            for title, space in [("Base A", a), ("Base B", b)]:
                libraries.append(
                    await call(
                        "POST",
                        "/knowledge-bases",
                        json={"title": title, "space_scope_id": space, "idempotency_key": title},
                    )
                )
            browser = await pw.chromium.launch(executable_path=executable)
            try:
                context = await browser.new_context()
                origin = str(client.base_url).rstrip("/")
                await context.add_cookies([
                    {"name": "powercontext_dashboard_token", "value": ADMIN_AUTHORIZATION_VALUE, "url": origin}
                ])
                page = await context.new_page()
                await page.goto(origin + "/dashboard/plugins/knowledge-base")
                frame = page.frame_locator("iframe")
                old_button = frame.get_by_role("button", name="Base A", exact=True)
                await old_button.wait_for()
                # A delayed/queued click from the previous view must also be harmless.
                await old_button.evaluate("button => { window.oldLibraryButton = button; }")
                await page.evaluate(
                    """({space, delayed}) => {
                      const original = window.fetch;
                      window.spaceDelay = {held: false};
                      window.fetch = async (...args) => {
                        const response = await original(...args);
                        const url = new URL(String(args[0]));
                        if (!window.spaceDelay.held && url.pathname.endsWith(delayed) &&
                            (delayed !== '/knowledge-bases' || url.searchParams.get('space_scope_id') === space)) {
                          window.spaceDelay.held = true;
                          await new Promise(resolve => { window.spaceDelay.release = resolve; });
                        }
                        return response;
                      };
                    }""",
                    {"space": b, "delayed": delayed},
                )
                await frame.locator("#spaces").select_option(b)
                await page.wait_for_function("window.spaceDelay.held")
                await playwright.expect(old_button).to_have_count(0)
                await frame.locator("body").evaluate("() => window.oldLibraryButton.click()")
                await playwright.expect(frame.locator("#library")).to_be_hidden()
                await page.evaluate("window.spaceDelay.release()")
                await frame.get_by_role("button", name="Base B", exact=True).click()
                await playwright.expect(frame.locator("#spaces")).to_have_value(b)
                await playwright.expect(frame.locator("#library-title")).to_have_text("Base B")
                await frame.locator("#files").set_input_files({
                    "name": "selected-space.md",
                    "mimeType": "text/markdown",
                    "buffer": b"Uploaded into the selected space",
                })
                selected_base = "/scopes/" + libraries[1]["scope_id"]
                docs = await wait_for(
                    lambda: call("GET", selected_base + "/documents"),
                    lambda d: bool(d["items"]),
                    log=tmp_path / "server.log",
                )
                assert docs["items"][0]["content"]["relative_path"] == "selected-space.md"
                old = await call("GET", "/scopes/" + libraries[0]["scope_id"] + "/documents")
                assert not old["items"] and not old["requests"]
            finally:
                await browser.close()

    asyncio.run(scenario())
