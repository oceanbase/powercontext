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

"""Two independent code generations with the same model and an auditable input difference."""

from __future__ import annotations

import json
import time
from typing import Any
from urllib.parse import urlsplit

import httpx
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from .adapter import SystemOneConfig
from .scenario import TASK

_INSTRUCTIONS = (
    "Implement the user's task. Return only Python source, without Markdown fences. "
    "This local example accepts one plain function cents(text: str) -> int and optional explicit, "
    "unaliased imports from decimal. Use only assignments, if, try/except, raise, return, arithmetic, "
    "comparisons, and calls to int, float, round, abs, isinstance, str, len, ValueError, TypeError, "
    "Decimal or the decimal methods quantize, is_finite, is_nan, is_infinite, to_integral_value, "
    "to_integral_exact, copy_abs, strip. No loops, helper functions, decorators, filesystem, "
    "network, globals or context mutation. Keep the implementation concise. "
    "When project context is provided, apply its project rules to the task."
)


class Settings(BaseSettings):
    """Explicit example configuration; never inherit another model's credentials."""

    model_config = SettingsConfigDict(extra="ignore", hide_input_in_errors=True)

    generation_endpoint: str = ""
    generation_model: str = ""
    generation_api_key: SecretStr = SecretStr("")
    jev_endpoint: str = ""
    jev_model: str = ""
    jev_api_key: SecretStr = SecretStr("")
    laya_endpoint: str = ""
    laya_model: str = ""
    laya_api_key: SecretStr = SecretStr("")
    laya_checkpoint: str = ""

    @property
    def generation_configured(self) -> bool:
        try:
            parsed = urlsplit(self.generation_endpoint)
        except ValueError:
            return False
        return bool(
            parsed.scheme in {"http", "https"}
            and parsed.hostname
            and not parsed.username
            and not parsed.password
            and not parsed.query
            and not parsed.fragment
            and self.generation_model.strip()
            and self.generation_api_key.get_secret_value().strip()
        )

    def provider(self, name: str) -> SystemOneConfig:
        if name not in {"jev", "laya"}:
            raise ValueError("Unknown review provider")  # noqa: TRY003
        return SystemOneConfig.model_validate({
            "provider": name,
            "endpoint": getattr(self, f"{name}_endpoint"),
            "model": getattr(self, f"{name}_model"),
            "api_key": getattr(self, f"{name}_api_key"),
            "timeout_seconds": 60.0,
        })


async def generate(client: httpx.AsyncClient, settings: Settings, context: str | None) -> dict[str, Any]:
    """Generate once; record the full non-secret request and actual response metadata."""
    if not settings.generation_configured:
        raise ValueError("Configure GENERATION_ENDPOINT, GENERATION_MODEL and GENERATION_API_KEY first.")  # noqa: TRY003
    user = TASK
    if context is not None:
        user += "\n\nProject context recalled by PowerContext:\n" + context
    payload = {
        "model": settings.generation_model,
        "messages": [{"role": "system", "content": _INSTRUCTIONS}, {"role": "user", "content": user}],
        "temperature": 0,
        "max_tokens": 1800,
    }
    started = time.monotonic()
    request = httpx.Request(
        "POST",
        settings.generation_endpoint,
        json=payload,
        headers={"Authorization": f"Bearer {settings.generation_api_key.get_secret_value()}"},
        extensions={"timeout": httpx.Timeout(120).as_dict()},
    )
    try:
        response = await client.send(request, auth=None, follow_redirects=False)
        response.raise_for_status()
        body = response.json()
        code, usage = _parse_response(body)
    except (httpx.HTTPError, OSError):
        raise RuntimeError("Generation service failed or timed out; check the endpoint and provider account.") from None  # noqa: TRY003
    except (ValueError, KeyError, IndexError, TypeError):
        message = "Generation service returned invalid or incomplete output; no code was substituted."
        raise RuntimeError(message) from None
    return {
        "code": code + "\n",
        "model": settings.generation_model,
        "usage": {key: usage.get(key) for key in ("prompt_tokens", "completion_tokens", "total_tokens")},
        "elapsed_ms": round((time.monotonic() - started) * 1000),
        "request": json.loads(json.dumps(payload, ensure_ascii=False)),
    }


def _parse_response(body: Any) -> tuple[str, dict[str, Any]]:
    """Require one complete source response; preserve its actual token counts."""
    if not isinstance(body, dict):
        raise TypeError("Generation response must be an object")  # noqa: TRY003
    choice = body["choices"][0]
    if not isinstance(choice, dict):
        raise TypeError("Generation choice must be an object")  # noqa: TRY003
    if choice.get("finish_reason") != "stop":
        raise ValueError("Generation did not finish normally")  # noqa: TRY003
    code = choice["message"]["content"]
    if not isinstance(code, str) or not code.strip() or len(code) > 16_000:
        raise ValueError("Generation did not return bounded source code")  # noqa: TRY003
    # Providers sometimes wrap otherwise valid source despite the instruction.
    # Remove only one enclosing fence; never repair or replace generated code.
    code = code.strip()
    if code.startswith(("```python\n", "```\n")) and code.endswith("\n```"):
        code = code.split("\n", 1)[1].rsplit("\n", 1)[0]
    usage = body.get("usage", {})
    if not isinstance(usage, dict):
        raise TypeError("Invalid generation usage")  # noqa: TRY003
    return code, usage
