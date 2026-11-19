# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Render passive processing evidence without loading saved-content pages."""

import asyncio
from urllib.parse import urlencode
from uuid import uuid4

from fastapi import Request
from fastapi.responses import HTMLResponse

from powercontext.server.dashboard.api import DashboardAPI, ReadError, segment
from powercontext.server.dashboard.preferences import presentation


async def artifact_processing_status(request: Request, scope: str, family: str) -> HTMLResponse:
    from powercontext.server.dashboard.routes import ENV

    api = DashboardAPI(request)
    try:
        async with asyncio.timeout(3):
            value = await api.read(f"/v1/scopes/{segment(scope)}/processing-status?" + urlencode({"family": family}))
        ctx = presentation(request)

        def destination(page, **params):
            return "/dashboard/" + page + "?" + urlencode({"scope": scope, "lang": ctx["language"], **params})

        return HTMLResponse(
            ENV.get_template("components/artifact-processing-result.html").render(
                **ctx,
                status=value,
                destination=destination,
                drawer_id="processing-" + uuid4().hex,
            ),
            headers={"Cache-Control": "no-store"},
        )
    except (ReadError, TimeoutError) as error:
        return HTMLResponse(
            status_code=error.status if isinstance(error, ReadError) else 503,
            headers={"Cache-Control": "no-store"},
        )
    finally:
        await api.client.aclose()
