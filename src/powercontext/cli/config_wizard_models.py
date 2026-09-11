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
# ruff: noqa: RUF001 -- Chinese punctuation is intentional in localized UI strings.

"""Collect only model connections needed by the selected wizard capabilities."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from powercontext.cli.config_wizard_ui import WizardUI

_PREFIX = "POWERCONTEXT_SERVER_INFERENCE_"
_LABELS = {
    "GENERATION": ("Generation", "后台整理模型"),
    "EMBEDDING": ("Embedding", "语义检索模型"),
    "RERANK": ("Reranking", "检索重排模型"),
}


@dataclass(frozen=True)
class _Provider:
    identifier: str
    en: str
    zh: str
    protocol: str
    endpoint: str
    generation_model: str
    embedding_model: str = ""


_PROVIDERS = (
    _Provider(
        "bailian",
        "Alibaba Cloud Bailian (DashScope)",
        "阿里云百炼（DashScope）",
        "openai-chat",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "qwen-plus",
        "text-embedding-v4",
    ),
    _Provider(
        "openai-chat",
        "OpenAI Chat Completions",
        "OpenAI Chat Completions",
        "openai-chat",
        "https://api.openai.com/v1",
        "gpt-4.1-mini",
        "text-embedding-3-small",
    ),
    _Provider(
        "openai-responses",
        "OpenAI Responses",
        "OpenAI Responses",
        "openai-responses",
        "https://api.openai.com/v1",
        "gpt-4.1-mini",
        "text-embedding-3-small",
    ),
    _Provider(
        "anthropic",
        "Anthropic",
        "Anthropic",
        "anthropic",
        "https://api.anthropic.com",
        "claude-sonnet-4-5",
    ),
    _Provider(
        "openrouter",
        "OpenRouter",
        "OpenRouter",
        "openai-chat",
        "https://openrouter.ai/api/v1",
        "deepseek/deepseek-v4-pro",
        "qwen/qwen3-embedding-4b",
    ),
    _Provider("custom", "Custom API-compatible service", "自定义 API 兼容服务", "", "", ""),
)
_PROVIDER_BY_ID = {provider.identifier: provider for provider in _PROVIDERS}


@dataclass(frozen=True)
class _Connection:
    protocol: str
    endpoint: str
    headers: str
    embedding_default: str = ""


def collect_models(
    ui: WizardUI,
    values: dict[str, str],
    *,
    generation: bool,
    embedding: bool,
    rerank: bool = False,
) -> dict[str, str | None]:
    """Return workload-scoped updates without changing values or contacting providers."""
    updates: dict[str, str | None] = {}
    shared: _Connection | None = None
    if generation:
        shared = _collect_role(ui, values, updates, "GENERATION")
    if embedding:
        _collect_role(ui, values, updates, "EMBEDDING", shared=shared)
    if rerank:
        if values.get(_PREFIX + "RERANK_MODEL", "").strip():
            _collect_role(ui, values, updates, "RERANK")
        elif generation and ui.confirm(
            "Use the Generation model for reranking? Choose No to configure a separate Reranking model.",
            "检索重排是否复用后台整理模型？选择“否”将继续配置单独的重排序模型。",
            default=True,
        ):
            _clear_overrides(ui, values, updates, "RERANK", include_model=True)
        else:
            _collect_role(ui, values, updates, "RERANK")
    return updates


def _collect_role(
    ui: WizardUI,
    values: dict[str, str],
    updates: dict[str, str | None],
    role: str,
    *,
    shared: _Connection | None = None,
) -> _Connection | None:
    en, zh = _LABELS[role]
    ui.section(en + " connection", zh + "连接")
    required = ("MODEL", "PROFILE_ID", "DIMENSION") if role == "EMBEDDING" else ("MODEL",)
    if all(values.get(_PREFIX + role + "_" + suffix, "").strip() for suffix in required) and ui.confirm(
        f"Keep the existing {en} configuration?",
        f"沿用已有的{zh}配置？",
        default=True,
    ):
        return _existing_connection(values, role)

    _clear_overrides(ui, values, updates, role)
    if (
        role == "EMBEDDING"
        and shared is not None
        and shared.protocol != "anthropic"
        and ui.confirm(
            "Reuse the Generation API address and API key for Embedding? The Embedding model is configured separately.",
            "Embedding 是否复用 Generation 的 API 地址和 API Key？Embedding 模型仍会单独配置。",
            default=True,
        )
    ):
        connection = shared
        model = ui.ask("Embedding model name", "语义检索模型名称", default=shared.embedding_default, required=True)
    else:
        connection, model = _new_connection(ui, role)

    adapter = "openai" if role == "EMBEDDING" or connection.protocol == "openai-responses" else connection.protocol
    updates[_PREFIX + role + "_MODEL"] = f"{adapter}:{model}"
    updates[_PREFIX + role + "_BASE_URL"] = connection.endpoint
    updates[_PREFIX + role + "_HEADERS"] = connection.headers
    if role == "EMBEDDING":
        _embedding_profile(ui, updates, model)
    return connection


def _new_connection(ui: WizardUI, role: str) -> tuple[_Connection, str]:
    en, zh = _LABELS[role]
    providers = [provider for provider in _PROVIDERS if role != "EMBEDDING" or provider.identifier != "anthropic"]
    identifier = ui.search(
        f"{en} service",
        f"{zh}服务商",
        [(provider.identifier, provider.en, provider.zh) for provider in providers],
        default=ui.text("openai-chat", "bailian"),
    )
    provider = _PROVIDER_BY_ID[identifier]
    protocol = provider.protocol
    if identifier == "custom":
        protocols = [("openai-chat", "OpenAI-compatible", "OpenAI 兼容 API")]
        if role != "EMBEDDING":
            protocols = [
                ("openai-chat", "OpenAI Chat Completions", "OpenAI Chat Completions"),
                ("openai-responses", "OpenAI Responses", "OpenAI Responses"),
                ("anthropic", "Anthropic Messages", "Anthropic Messages"),
            ]
        protocol = ui.choose("API protocol", "API 协议", protocols, default="openai-chat")
    if identifier == "bailian":
        ui.say(
            "The default is Bailian's China standard API endpoint. Change it for another region or plan.",
            "默认地址是百炼中国区标准 API；其他地域或套餐请改为对应地址。",
        )
    ui.say(
        "Use API service credentials; a ChatGPT or Claude subscription is not an API key. No model request is sent here.",
        "请使用模型 API 服务凭据；ChatGPT 或 Claude 订阅不是 API Key。本步骤不会调用模型。",
    )
    endpoint = _endpoint(ui, provider.endpoint)
    model = ui.ask(
        f"{en} model name",
        f"{zh}名称",
        default=provider.embedding_model if role == "EMBEDDING" else provider.generation_model,
        required=True,
    )
    key = _api_key(ui)
    headers = {"x-api-key": key} if protocol == "anthropic" else {"Authorization": "Bearer " + key}
    return _Connection(protocol, endpoint, json.dumps(headers, ensure_ascii=False), provider.embedding_model), model


def _endpoint(ui: WizardUI, default: str) -> str:
    while True:
        endpoint = ui.ask("API base URL", "API 基础地址", default=default, required=True).strip()
        if _valid_endpoint(endpoint):
            return endpoint.rstrip("/")
        ui.say(
            "Enter an http(s) URL with a hostname and no embedded credentials, query parameters, or fragment.",
            "请输入包含主机名的 HTTP(S) 地址，不能包含用户名密码、查询参数或片段。",
        )


def _valid_endpoint(endpoint: str) -> bool:
    try:
        parts = urlsplit(endpoint)
        return bool(
            parts.scheme in {"http", "https"}
            and parts.hostname
            and parts.port != 0
            and parts.username is None
            and parts.password is None
            and not parts.query
            and not parts.fragment
            and "?" not in endpoint
            and "#" not in endpoint
            and not any(character.isspace() or ord(character) < 32 for character in endpoint)
        )
    except ValueError:
        return False


def _api_key(ui: WizardUI) -> str:
    while True:
        key = ui.ask("API key (hidden)", "API Key（隐藏输入）", secret=True, required=True)
        if key.strip() and not any(ord(character) < 32 or ord(character) == 127 for character in key):
            return key
        ui.say("API keys must not contain control characters.", "API Key 不能包含控制字符。")


def _embedding_profile(ui: WizardUI, updates: dict[str, str | None], model: str) -> None:
    defaults = {
        "text-embedding-3-small": 1536,
        "text-embedding-3-large": 3072,
        "text-embedding-v4": 1024,
        "qwen/qwen3-embedding-4b": 2560,
    }
    dimension = defaults.get(model)
    if dimension is None:
        ui.say(
            "Vector dimensions control the size of stored vectors. Enter the dimension documented by this model.",
            "向量维度决定每条向量的大小，请填写该模型文档标明的维度。",
        )
        dimension = ui.integer("Embedding dimensions", "向量维度", default=1024, minimum=1)
    model_slug = re.sub(r"[^a-z0-9]+", "-", "openai:" + model.lower()).strip("-")
    profile = f"{model_slug}-{dimension}-unit-v1"
    updates[_PREFIX + "EMBEDDING_DIMENSION"] = str(dimension)
    updates[_PREFIX + "EMBEDDING_PROFILE_ID"] = profile
    updates[_PREFIX + "EMBEDDING_NORMALIZATION"] = "unit"


def _clear_overrides(
    ui: WizardUI,
    values: dict[str, str],
    updates: dict[str, str | None],
    role: str,
    *,
    include_model: bool = False,
) -> None:
    suffixes = ("BASE_URL", "HEADERS", "MODEL_SETTINGS") + (("MODEL",) if include_model else ())
    existing = [_PREFIX + role + "_" + suffix for suffix in suffixes if _PREFIX + role + "_" + suffix in values]
    if existing:
        ui.say(
            "Replacing this connection also clears its previous endpoint, credentials and request settings; "
            "shared provider environment variables are preserved.",
            "更换此连接时会清除该模型原有的地址、凭据和请求设置；共享的服务商环境变量会保留。",
        )
        updates.update(dict.fromkeys(existing))


def _existing_connection(values: dict[str, str], role: str) -> _Connection | None:
    """Offer sharing only when a retained connection can be reproduced safely."""
    model = values.get(_PREFIX + role + "_MODEL", "")
    adapter, _, _ = model.partition(":")
    if adapter not in {"openai", "openai-chat", "openai-responses"}:
        return None
    endpoint = values.get(_PREFIX + role + "_BASE_URL") or values.get("OPENAI_BASE_URL") or "https://api.openai.com/v1"
    if not _valid_endpoint(endpoint):
        return None
    headers = values.get(_PREFIX + role + "_HEADERS")
    if not headers:
        key = values.get("OPENAI_API_KEY")
        if not key:
            return None
        headers = json.dumps({"Authorization": "Bearer " + key}, ensure_ascii=False)
    try:
        parsed = json.loads(headers)
    except ValueError:
        return None
    if not isinstance(parsed, dict) or not parsed or not all(isinstance(value, str) for value in parsed.values()):
        return None
    default = next((provider.embedding_model for provider in _PROVIDERS if provider.endpoint == endpoint), "")
    protocol = "openai-responses" if adapter == "openai" else adapter
    return _Connection(protocol, endpoint, headers, default)
