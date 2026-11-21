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

"""OCR failures must not publish empty revisions or block later processing."""

import asyncio
import io
import shutil

import pytest
from PIL import Image, ImageDraw, ImageFont

from tests.e2e.plugin_server import running_server, wait_for
from tests.e2e.test_knowledge_base import FIXTURE_EMBEDDING, PLUGIN, P


def test_ocr_without_text_fails_and_preserves_the_published_document(tmp_path):
    pytest.importorskip("rapidocr")
    plugins = tmp_path / "plugins"
    shutil.copytree(PLUGIN, plugins / "knowledge-base")
    entry = plugins / "knowledge-base" / "plugin.py"
    entry.write_text(entry.read_text() + FIXTURE_EMBEDDING)
    blank = io.BytesIO()
    Image.new("RGB", (300, 150), "white").save(blank, format="PNG")
    image = Image.new("RGB", (1000, 240), "white")
    ImageDraw.Draw(image).text(
        (30, 40), "Verify backups before restarting.", font=ImageFont.load_default(size=36), fill="black"
    )
    valid = io.BytesIO()
    image.save(valid, format="PNG")

    async def scenario():
        async with running_server(
            tmp_path, plugins, extra_env={"POWERCONTEXT_KB_DATA_DIR": str(tmp_path / "data")}
        ) as client:

            async def call(method, path, **kwargs):
                response = await client.request(method, P + path, **kwargs)
                assert response.is_success, response.text
                return response.json()

            space = (await call("GET", "/me/default-space"))["space_scope_id"]
            kb = await call(
                "POST",
                "/knowledge-bases",
                json={"title": "Images", "space_scope_id": space, "idempotency_key": "images"},
            )
            base = "/scopes/" + kb["scope_id"]

            async def upload(path, key, data):
                receipt = await call(
                    "POST", base + "/uploads", params={"relative_path": path, "idempotency_key": key}, content=data
                )
                status = await wait_for(
                    lambda: call("GET", base + "/imports/" + receipt["request_id"]),
                    lambda r: r["state"] in {"failed", "completed"},
                    log=tmp_path / "server.log",
                )
                return receipt, status

            _, empty = await upload("empty.png", "empty", blank.getvalue())
            assert empty["state"] == "failed" and "no recognized text" in empty["error"]
            assert empty["current_revision"] is None
            assert not (await call("GET", base + "/documents"))["items"]

            receipt, original = await upload("image.png", "valid", valid.getvalue())
            assert original["state"] == "completed" and original["current_revision"] == 1
            before = await call("POST", base + "/retrieve", json={"query": "backups", "mode": "fts"})
            assert before["hits"]
            _, rejected = await upload("image.png", "blank-update", blank.getvalue())
            assert rejected["state"] == "failed" and "no recognized text" in rejected["error"]
            assert rejected["current_revision"] == 1
            after = await call("POST", base + "/retrieve", json={"query": "backups", "mode": "fts"})
            assert after == before
            documents = (await call("GET", base + "/documents"))["items"]
            assert len(documents) == 1 and documents[0]["content"]["chunk_count"] > 0
            history = await client.get(
                f"/v1/scopes/{kb['scope_id']}/artifacts/document/{receipt['document_id']}/revisions/2"
            )
            assert history.status_code == 404
            _, later = await upload("later.md", "later", b"A subsequent valid document")
            assert later["state"] == "completed"

    asyncio.run(scenario())
