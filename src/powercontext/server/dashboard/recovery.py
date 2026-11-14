# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Submit a retry for an exact observed block without replacing domain recovery rules."""

from pydantic import ValidationError

from powercontext.http._generated.models import RetryTopicMemoryProcessingRequest, RetryTopicMemoryProcessingResponse
from powercontext.server.dashboard.errors import ReadError
from powercontext.server.dashboard.session import same_origin


async def submit_topic_retry(api, request):
    if not same_origin(request):
        raise ReadError(403, "cross_origin_request")
    if len(await request.body()) > 2000:
        raise ReadError(413, "invalid_request")
    form = await request.form()
    if set(form) != {"scope", "block_id"} or any(len(form.getlist(key)) != 1 for key in form):
        raise ReadError(422, "invalid_request")
    try:
        selected = RetryTopicMemoryProcessingRequest(scope_id=str(form["scope"]), block_id=str(form["block_id"]))
    except ValidationError as error:
        raise ReadError(422, "invalid_request") from error
    value = await api.read("/v1/topic-memory/processing/retry", selected.model_dump(mode="json"))
    try:
        RetryTopicMemoryProcessingResponse.model_validate(value)
    except ValidationError as error:
        raise ReadError(503, "service_unavailable") from error
    return selected.scope_id
