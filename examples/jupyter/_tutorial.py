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

"""Local environment and display helpers; tutorial operations stay in the notebooks."""

from __future__ import annotations

import asyncio
import atexit
import json
import os
import socket
import threading
import time
from collections.abc import Mapping, Sequence
from html import escape
from pathlib import Path
from uuid import uuid4

import uvicorn
from dotenv import load_dotenv
from IPython.display import HTML, display
from pydantic import BaseModel, SecretStr

from powercontext.builtin.persistence.oceanbase import OceanBaseConfig
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime.config import ExternalSkillsConfig, InferenceConfig, RuntimeConfig
from powercontext.client import PowerContextClient
from powercontext.server.factory import create_server_app
from powercontext.server.settings import (
    AccessControlConfig,
    BearerAuthConfig,
    HttpConfig,
    McpConfig,
    MetricsConfig,
    ServerSettings,
    TracingConfig,
)

DIRECTORY = Path(__file__).resolve().parent


def show(value: BaseModel | Mapping[str, object] | Sequence[object]) -> None:
    """Display public result data. Never pass provider settings or credentials here."""
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json", by_alias=True)
    print(json.dumps(value, ensure_ascii=False, indent=2))


def table(rows: Sequence[Mapping[str, object]]) -> None:
    """Render a small, escaped result table without requiring pandas."""
    if not rows:
        print("没有条目。")
        return
    columns = list(rows[0])
    header = "".join(f"<th>{escape(str(column))}</th>" for column in columns)
    body = "".join(
        "<tr>" + "".join(f"<td>{escape(str(row.get(column, '')))}</td>" for column in columns) + "</tr>" for row in rows
    )
    display(HTML(f"<table><thead><tr>{header}</tr></thead><tbody>{body}</tbody></table>"))


def load_model_environment() -> None:
    """Read the explicitly selected file, or this tutorial directory's .env."""
    configured = os.environ.get("POWERCONTEXT_NOTEBOOK_ENV_FILE")
    path = Path(configured).expanduser().resolve() if configured else DIRECTORY / ".env"
    if configured and not path.is_file():
        raise RuntimeError("模型配置文件不存在，请检查 POWERCONTEXT_NOTEBOOK_ENV_FILE。")  # noqa: TRY003
    if path.is_file():
        load_dotenv(path, override=False)


def inference_settings(features: Sequence[str]) -> InferenceConfig:
    """Enable only the requested providers; ignore the user's database and scheduler."""
    if not features:
        return InferenceConfig()
    load_model_environment()
    prefix = "POWERCONTEXT_SERVER_INFERENCE_"
    values = {}
    for key, value in os.environ.items():
        if not key.startswith(prefix) or not value:
            continue
        field = key.removeprefix(prefix).lower()
        if not any(field.startswith(feature + "_") for feature in features):
            continue
        values[field] = json.loads(value) if field.endswith(("_headers", "_model_settings")) else value
    for feature in features:
        model = values.get(feature + "_model")
        if not model or model == "test":
            raise RuntimeError(  # noqa: TRY003
                f"本篇需要真实 {feature} 模型。请按 README 配置 .env；本教程不会用测试模型代替真实调用。"
            )
    return InferenceConfig.model_validate(values)


def chat_settings() -> dict[str, object]:
    """Connection plumbing for the OpenAI-compatible agent in lesson 10."""
    load_model_environment()
    model = os.environ.get("POWERCONTEXT_NOTEBOOK_CHAT_MODEL")
    if not model:
        configured = os.environ.get("POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL", "")
        if configured.startswith(("openai-chat:", "openai:")):
            model = configured.partition(":")[2]
    key = os.environ.get("OPENAI_API_KEY")
    if not model or not key:
        raise RuntimeError(  # noqa: TRY003
            "第 10 篇需要 POWERCONTEXT_NOTEBOOK_CHAT_MODEL 和 OPENAI_API_KEY；兼容服务可设置 OPENAI_BASE_URL。"
        )
    settings: dict[str, object] = {"model": model, "api_key": key, "temperature": 0, "timeout": 90, "max_retries": 0}
    if base_url := os.environ.get("OPENAI_BASE_URL"):
        settings["base_url"] = base_url
    return settings


class Tutorial:
    """Own a loopback Server and Client, using SQLite or an explicit OceanBase test database."""

    def __init__(self, lesson: str, features: Sequence[str]) -> None:
        root = Path(os.environ.get("POWERCONTEXT_NOTEBOOK_DATA_DIR", DIRECTORY / ".powercontext"))
        self.directory = root / f"{lesson}-{uuid4().hex[:12]}"
        self.directory.mkdir(parents=True, exist_ok=False)
        self.run_id = self.directory.name
        load_model_environment()
        oceanbase_url = os.environ.get("POWERCONTEXT_NOTEBOOK_OCEANBASE_URL")
        self.database = (
            OceanBaseConfig(url=SecretStr(oceanbase_url))
            if oceanbase_url
            else SQLiteConfig(url=f"sqlite+aiosqlite:///{self.directory / 'tutorial.db'}")
        )
        self.inference = inference_settings(features)
        # Advanced lessons opt into real Server features explicitly, then restart.
        self.settings_overrides: dict[str, object] = {}
        self.app_options: dict[str, object] = {}
        self.client_token: str | None = None
        self.base_url = ""
        self.client: PowerContextClient | None = None
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None
        self._listener: socket.socket | None = None

    @classmethod
    async def start(cls, lesson: str, *, features: Sequence[str] = ()) -> Tutorial:
        lab = await asyncio.to_thread(cls, lesson, features)
        try:
            await asyncio.to_thread(lab._start_server)
            lab.client = PowerContextClient(lab.base_url, timeout=180)
            capabilities = await lab.client.get_capabilities()
        except BaseException:
            await lab.close()
            raise
        table([
            {"检查": "存储后端", "结果": lab.database.kind},
            {"检查": "本次实验", "结果": lab.run_id},
            {"检查": "Server", "结果": lab.base_url},
            {"检查": "自动提取", "结果": capabilities.memory_extraction},
            {"检查": "检索方式", "结果": ", ".join(capabilities.search_modes)},
        ])
        return lab

    def _start_server(self) -> None:
        settings = ServerSettings(
            workspace=self.directory,
            database=self.database,
            http=HttpConfig(host="127.0.0.1"),
            auth=BearerAuthConfig(enabled=False),
            access=AccessControlConfig(mode="disabled"),
            inference=self.inference,
            runtime=RuntimeConfig(schedule_seconds=None, experience_schedule_seconds=None),
            external_skills=ExternalSkillsConfig(),
            mcp=McpConfig(enabled=False),
            metrics=MetricsConfig(enabled=False),
            tracing=TracingConfig(enabled=False),
        )
        if self.settings_overrides:
            settings = ServerSettings.model_validate({**dict(settings), **self.settings_overrides})
        app = create_server_app(settings=settings, scheduler_path=self.directory / "scheduler.db", **self.app_options)
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        self._listener = listener
        self.base_url = f"http://127.0.0.1:{listener.getsockname()[1]}"
        server = uvicorn.Server(uvicorn.Config(app, log_level="critical", access_log=False, lifespan="on"))
        self._server = server
        self._thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        self._thread.start()
        atexit.register(self._stop_server)
        deadline = time.monotonic() + 45
        while self._thread.is_alive() and not server.started and time.monotonic() < deadline:
            time.sleep(0.05)
        if not server.started:
            self._stop_server()
            raise RuntimeError("本地 Server 启动失败，请检查单元格错误与 README 故障排查。")  # noqa: TRY003

    def _stop_server(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=15)
            if self._thread.is_alive():
                raise RuntimeError("Server 尚未退出，请关闭当前 Kernel 后重试。")  # noqa: TRY003
        if self._listener is not None:
            self._listener.close()
        self._server = None
        self._thread = None
        self._listener = None
        atexit.unregister(self._stop_server)

    async def restart(self) -> None:
        """Restart the actual Server against the same database."""
        await self.close()
        await asyncio.to_thread(self._start_server)
        self.client = PowerContextClient(self.base_url, timeout=180, token=self.client_token)

    async def close(self) -> None:
        """Release the client and listener; retain this run's files for inspection."""
        if self.client is not None:
            await self.client.aclose()
            self.client = None
        await asyncio.to_thread(self._stop_server)
