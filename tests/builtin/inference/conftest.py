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

import httpx
import pytest

from powercontext.builtin.inference.kimi import KimiOAuthAuth, login_kimi
from powercontext.builtin.inference.qwen import QwenOAuthAuth, login_qwen


@pytest.fixture(
    params=[
        pytest.param(
            ("qwen", QwenOAuthAuth, login_qwen, "https://api.qwen.ai/compatible-mode/v1/chat/completions"), id="qwen"
        ),
        pytest.param(("kimi", KimiOAuthAuth, login_kimi, "https://api.kimi.com/coding/v1/chat/completions"), id="kimi"),
    ]
)
def coding_subscription(request):
    return request.param


@pytest.fixture
def oauth_http(monkeypatch):
    """Control the OAuth SDK's public HTTP boundary across provider rewrites."""
    original = httpx.AsyncClient.__init__

    def configure(handler):
        def initialize(client, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            original(client, *args, **kwargs)

        monkeypatch.setattr(httpx.AsyncClient, "__init__", initialize)

    return configure
