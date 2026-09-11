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

"""Storage-first guided environment generation, with no deployment side effects."""

from __future__ import annotations

import json
import os
import re
import secrets
import shlex
import socket
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from textwrap import dedent

import typer
from pydantic import ValidationError
from sqlalchemy.engine import URL, make_url

from powercontext.builtin.runtime.processing_registry import (
    RECOMMENDED_PROCESSING_SCHEDULE_SECONDS,
    RECOMMENDED_PROFILE_CRON,
    RECOMMENDED_PROFILE_TIMEZONE,
)
from powercontext.cli.config_wizard_agents import AGENT_SPEC_BY_ID, AGENT_SPECS, AgentSpec, preferred_agent
from powercontext.cli.config_wizard_document import read_sqlite_summary, update_document
from powercontext.cli.config_wizard_installation import installation_source
from powercontext.cli.config_wizard_models import collect_models
from powercontext.cli.config_wizard_seekdb import (
    SeekDBInstallTask,
    inspect_seekdb_dependency,
    start_seekdb_install,
)
from powercontext.cli.config_wizard_ui import WizardUI, choose_language
from powercontext.cli.env_file import EnvironmentFileError, parse_environment
from powercontext.client.settings import normalize_server_url
from powercontext.paths import default_database_path, default_seekdb_path, sqlite_url

SERVER = "POWERCONTEXT_SERVER_"
RUNTIME = f"{SERVER}RUNTIME_"
INFERENCE = f"{SERVER}INFERENCE_"
CLIENT = "POWERCONTEXT_CLIENT_"
FEATURES = (
    ("memory", "Automatic Memory extraction", "后台 Memory 提取"),
    ("topic-memory", "Automatic Topic Memory", "Topic Memory 自动整理"),
    ("vector", "Semantic search (Embedding)", "语义检索（Embedding）"),
    ("experience", "Experience candidates from task outcomes", "从任务结果孵化 Experience 候选"),
    ("profile", "Scope Profile generation", "Scope Profile 生成"),
    ("skill", "On-demand Skill generation", "按需生成 Skill 候选"),
    ("rerank", "Memory reranking (optional)", "Memory 重排（可选）"),
)


class WizardInputError(ValueError):
    """A safe, user-facing message that does not include configuration values."""


@dataclass
class Wizard:
    """Collected answers and exact-key changes awaiting the final write."""

    ui: WizardUI
    original: dict[str, str]
    values: dict[str, str]
    updates: dict[str, str | None] = field(default_factory=dict)
    client: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    features: set[str] = field(default_factory=set)
    client_only: bool = False
    advanced: bool = False
    scenario: str = "local"
    forwarded_address: str = ""
    ssh_tunnel_command: str = ""
    generated_auth_token: str = ""
    agents: tuple[str, ...] = ()
    agent_addresses: dict[str, str] = field(default_factory=dict)
    agent_settings: dict[str, dict[str, object]] = field(default_factory=dict)
    planned_scopes: dict[str, str] = field(default_factory=dict)
    storage_summary: dict[str, object] = field(default_factory=dict)
    seekdb_install_task: SeekDBInstallTask | None = None

    def patch(self, updates: dict[str, str | None]) -> None:
        self.updates.update(updates)
        for key, value in updates.items():
            if value is None:
                self.values.pop(key, None)
            else:
                self.values[key] = value

    def note(self, en: str, zh: str) -> None:
        message = self.ui.text(en, zh)
        if message not in self.notes:
            self.notes.append(message)
        self.ui.say(en, zh)


def run_wizard(output: Path, *, language: str | None = None, advanced: bool = False) -> None:
    """Collect answers and write files only after explicit final confirmation."""

    from powercontext.cli.config import ConfigError

    output = output.expanduser().absolute()
    state: Wizard | None = None
    try:
        _check_output(output)
        content = output.read_text(encoding="utf-8") if output.exists() else ""
        original = parse_environment(content, source=str(output))
        match = re.search(r"(?m)^# powercontext-wizard-language=(en|zh)\s*$", content)
        chosen = choose_language(language, None if match is None else match.group(1))
        ui = WizardUI(chosen)
        state = Wizard(ui=ui, original=original, values=dict(original), advanced=advanced)
        ui.section("PowerContext configuration wizard", "PowerContext 配置向导")
        ui.say(
            "Generate files, then review the startup and acceptance steps. No services will start here.",
            "生成配置文件，并提供启动与验收步骤。本向导不会启动服务。",
        )
        _storage(state)
        if state.client_only:
            _agents(state)
        else:
            mode = "configure"
            if content:
                mode = ui.choose(
                    "Existing configuration: how would you like to continue?",
                    "发现已有配置：接下来怎么做？",
                    [
                        ("reuse", "Keep other existing settings and review", "保留其余已有配置，直接检查并保存"),
                        ("edit", "Edit selected modules", "只修改指定模块"),
                        (
                            "configure",
                            "Confirm every setting (existing values are defaults)",
                            "逐项重新确认（现有值作为默认）",
                        ),
                    ],
                    default="reuse",
                )
            if mode == "configure":
                _scenario(state)
                _capabilities(state)
                _network(state)
                _models(state)
                _processing(state)
                if advanced:
                    _advanced(state)
                _agents(state)
            elif mode == "edit":
                _edit_modules(state)
        _finish(state, output, content)
    except (ConfigError, EnvironmentFileError, OSError, ValueError) as error:
        if isinstance(error, (WizardInputError, EnvironmentFileError, OSError)):
            message = str(error)
        else:
            message = "Invalid configuration; review the selected module / 配置无效，请检查所选模块"
        typer.echo(f"Configuration error / 配置错误: {message}", err=True)
        raise typer.Exit(2) from None
    finally:
        if state is not None and state.seekdb_install_task is not None and not state.seekdb_install_task.done():
            state.seekdb_install_task.cancel()


def _check_output(path: Path) -> None:
    if path.is_symlink() or (path.exists() and not path.is_file()):
        message = "Output must be a regular file, not a directory or symbolic link / 输出必须是普通文件"
        raise WizardInputError(message)


def _storage(state: Wizard) -> None:
    ui = state.ui
    ui.section("1. Storage or existing Server", "1. 存储或已有 Server")
    kind = ui.choose(
        "Where should the data live?",
        "数据准备存在哪里，或连接哪个已有服务？",
        [
            ("sqlite", "Local SQLite file", "本机 SQLite 文件"),
            ("seekdb", "Local embedded seekdb", "本机嵌入式 seekdb"),
            ("oceanbase", "OceanBase database", "OceanBase 数据库"),
            ("remote", "Connect to an existing PowerContext Server", "接入已有 PowerContext Server"),
        ],
        default=state.values.get(
            f"{SERVER}DATABASE_KIND", "remote" if f"{CLIENT}SERVER_URL" in state.values else "sqlite"
        ),
    )
    if kind == "remote":
        _existing_server(state)
        return
    state.patch({f"{SERVER}DATABASE_KIND": kind})
    if kind == "sqlite":
        previous = state.values.get(f"{SERVER}DATABASE_URL")
        default_path = str(default_database_path())
        if previous and previous.startswith("sqlite"):
            default_path = make_url(previous).database or default_path
        path = (
            Path(ui.ask("SQLite file", "SQLite 文件位置", default=default_path, required=True)).expanduser().absolute()
        )
        state.patch({f"{SERVER}DATABASE_URL": sqlite_url(path), f"{SERVER}DATABASE_PATH": None})
        summary = read_sqlite_summary(path)
        state.storage_summary = summary
        status = summary["status"]
        if status == "new":
            ui.say(
                "New location; the database will be created when you start Server.",
                "新数据位置；启动 Server 时才会创建数据库。",
            )
        elif status == "existing":
            ui.say("Existing database found (read-only inspection).", "发现已有数据库（只读检查）。")
            state.note(
                "Keep the original model and index settings when reusing data.",
                "沿用数据时，请保留匹配的原模型与索引配置。",
            )
        else:
            state.note(
                "Database compatibility is unverified; inspect it before starting Server.",
                "尚未确认数据库兼容性；启动服务前需要检查。",
            )
    elif kind == "seekdb":
        path = (
            Path(
                ui.ask(
                    "seekdb directory",
                    "seekdb 数据目录",
                    default=state.values.get(f"{SERVER}DATABASE_PATH", str(default_seekdb_path())),
                    required=True,
                )
            )
            .expanduser()
            .absolute()
        )
        state.patch({f"{SERVER}DATABASE_PATH": str(path), f"{SERVER}DATABASE_URL": None})
        _prepare_seekdb_dependency(state)
    else:
        previous = state.values.get(f"{SERVER}DATABASE_URL", "")
        if not previous.startswith("mysql+aoceanbase:"):
            previous = ""
        if previous and ui.confirm("Keep the existing database connection?", "保留已有数据库连接？"):
            state.patch({f"{SERVER}DATABASE_PATH": None})
            return
        host = ui.ask("OceanBase host", "OceanBase 地址", default="127.0.0.1", required=True)
        port = ui.integer("Database port", "数据库端口", default=2881, maximum=65535)
        database = ui.ask("Database name", "数据库名称", default="powercontext", required=True)
        username = ui.ask("Database user", "数据库用户名", required=True)
        password = ui.ask("Database password", "数据库密码", secret=True)
        url = URL.create(
            "mysql+aoceanbase",
            username=username,
            password=password,
            host=host,
            port=port,
            database=database,
            query={"charset": "utf8mb4"},
        )
        state.patch({
            f"{SERVER}DATABASE_URL": url.render_as_string(hide_password=False),
            f"{SERVER}DATABASE_PATH": None,
        })
        state.note(
            "OceanBase connection and schema compatibility have not been tested.",
            "尚未验证 OceanBase 连接及表结构兼容性。",
        )


def _existing_server(state: Wizard) -> None:
    if any(key.startswith(SERVER) for key in state.original):
        message = "Use a different --output for client-only settings / 请用另一个 --output 保存纯客户端配置"
        raise WizardInputError(message)
    state.client_only = True
    address = _server_url(state.ui, state.values.get(f"{CLIENT}SERVER_URL", "https://"))
    token = state.ui.ask(
        "Server access token (empty for no authentication; Enter keeps an existing token)",
        "Server 访问令牌（无需认证时留空；已有令牌时回车保留）",
        default=state.values.get(f"{CLIENT}API_TOKEN", ""),
        secret=True,
    )
    state.client = {f"{CLIENT}SERVER_URL": address}
    if token:
        state.client[f"{CLIENT}API_TOKEN"] = token
    state.note("Server capabilities and connectivity have not been tested.", "尚未验证已有服务的能力与连通性。")


def _prepare_seekdb_dependency(state: Wizard) -> None:
    dependency = inspect_seekdb_dependency()
    if dependency.status == "ready":
        state.ui.say(
            f"seekdb dependency is ready, version {dependency.version}.",
            f"seekdb 依赖已就绪，版本 {dependency.version}。",
        )
        return
    if dependency.status == "unavailable" or dependency.plan is None:
        state.note(
            f"seekdb dependency cannot be installed automatically: {dependency.reason}.",
            f"seekdb 依赖无法自动安装：{dependency.reason}。",
        )
        return
    if not state.ui.confirm(
        "The seekdb dependency is missing (about 110–130 MB). Install it in the background while you continue?",
        "尚未安装 seekdb 依赖（约 110–130 MB）。是否在后台安装并继续配置？",
    ):
        state.note(
            "seekdb dependency installation was skipped; install it before starting Server.",
            "已跳过 seekdb 依赖安装；启动 Server 前必须先完成安装。",
        )
        return
    state.seekdb_install_task = start_seekdb_install(dependency.plan)
    if dependency.plan.prefer_aliyun:
        state.ui.say(
            "China timezone detected; seekdb dependency installation started in the background using the Aliyun mirror.",
            "检测到中国时区；seekdb 依赖已通过阿里云镜像在后台开始安装，你可以继续配置。",
        )
    else:
        state.ui.say(
            "seekdb dependency installation started in the background; you can continue configuring.",
            "seekdb 依赖已在后台开始安装，你可以继续配置。",
        )


def _scenario(state: Wizard) -> None:
    ui = state.ui
    ui.section("2. Usage scenario", "2. 使用场景")
    ui.say(f"Current machine: {socket.gethostname()}", f"当前机器：{socket.gethostname()}")
    old_host = state.values.get(f"{SERVER}HTTP_HOST", "127.0.0.1")
    state.scenario = ui.choose(
        "Where will your Agent and browser run?",
        "Agent 和浏览器会在哪里使用这个服务？",
        [
            ("local", "Only on this machine", "只在当前机器"),
            ("remote", "Access from other devices", "从其他设备访问"),
        ],
        default="local" if old_host in {"127.0.0.1", "localhost", "::1"} else "remote",
    )


def _capabilities(state: Wizard) -> None:
    ui = state.ui
    ui.section("3. Memory capabilities", "3. 记忆能力")
    profile = ui.choose(
        "Which capabilities do you want?",
        "希望启用哪些记忆能力？",
        [
            (
                "full",
                "Full memory capabilities (recommended)",
                "完整记忆能力（推荐）",
            ),
            (
                "base",
                "Basic memory (no additional model API)",
                "基础记忆（无需额外模型 API）",
            ),
            ("custom", "Choose capabilities individually (advanced)", "自行选择能力（高级）"),
        ],
        default="full",
    )
    if profile == "full":
        state.features = {feature for feature, _, _ in FEATURES if feature != "rerank"}
        ui.say(
            "Enabled: Memory, Topic Memory, Profile, Experience, Skill, and semantic retrieval. "
            "Generation and Embedding model APIs are required.",
            "将启用：Memory、Topic Memory、Profile、Experience、Skill 和语义检索；"
            "需要 Generation 与 Embedding 模型 API。",
        )
    elif profile == "base":
        state.features = set()
    else:
        state.features = {name for name, en, zh in FEATURES if ui.confirm(en + "?", zh + "？", default=False)}
    if profile == "base":
        ui.say(
            "Ask your Agent to save a memory explicitly; captured conversations are not automatically extracted.",
            "可让 Agent 显式保存记忆；采集的普通对话不会因此自动提取。",
        )
    changes: dict[str, str | None] = {}
    for family in ("memory", "topic-memory", "experience"):
        key = f"{RUNTIME}{family.upper().replace('-', '_')}_SCHEDULE_SECONDS"
        recommended = RECOMMENDED_PROCESSING_SCHEDULE_SECONDS[family]
        changes[key] = state.values.get(key, str(recommended)) if family in state.features else None
    changes[f"{RUNTIME}SCHEDULE_SECONDS"] = None
    changes[f"{RUNTIME}PROFILE_SCHEDULE_ENABLED"] = str("profile" in state.features).lower()
    changes[f"{RUNTIME}MEMORY_RERANK_ENABLED"] = str("rerank" in state.features).lower()
    if not state.original:
        changes[f"{RUNTIME}ARTIFACT_PROCESSING_FAMILIES"] = json.dumps(
            sorted(state.features & {"memory", "topic-memory", "experience", "profile", "skill"})
        )
    elif state.values.get(f"{RUNTIME}ARTIFACT_PROCESSING_FAMILIES"):
        declared = json.loads(state.values[f"{RUNTIME}ARTIFACT_PROCESSING_FAMILIES"])
        if not isinstance(declared, list) or any(not isinstance(item, str) for item in declared):
            message = "ARTIFACT_PROCESSING_FAMILIES must be a list of names / 必须使用名称列表"
            raise WizardInputError(message)
        changes[f"{RUNTIME}ARTIFACT_PROCESSING_FAMILIES"] = json.dumps(
            sorted(set(declared) | (state.features & {"memory", "topic-memory", "experience", "profile", "skill"}))
        )
    state.patch(changes)
    if state.original:
        state.note(
            "Existing model credentials are retained; disabled schedules stop new automatic work, not accepted work.",
            "保留原模型凭据；关闭调度仅停止新增定时工作，已接受任务可能继续恢复。",
        )
        if "vector" not in state.features and state.values.get(f"{INFERENCE}EMBEDDING_MODEL"):
            state.note(
                "Existing semantic search is retained to protect its index; recalls may still call the Embedding API.",
                "为保护已有索引，原语义检索配置仍保留；召回时仍可能调用 Embedding API。",
            )


def _server_url(ui: WizardUI, default: str) -> str:
    try:
        default = normalize_server_url(default) if default else ""
    except ValueError:
        default = ""
    ui.say(
        "Enter the complete address, for example https://memory.example.com (not only https://).",
        "请输入完整地址，例如 https://memory.example.com（不能只填 https://）。",
    )
    while True:
        value = ui.ask("Client-visible Server URL", "客户端实际访问的 Server URL", default=default, required=True)
        try:
            return normalize_server_url(value)
        except ValueError:
            ui.say(
                "Use HTTPS for remote access, or HTTP with localhost/127.0.0.1; no credentials, query or fragment.",
                "远程访问请使用 HTTPS，或通过转发使用本机 HTTP；URL 不得包含凭据、查询或片段。",
            )


def _stored_network_port(state: Wizard) -> tuple[int, bool]:
    try:
        port = int(state.values.get(f"{SERVER}HTTP_PORT", "8000"))
    except ValueError:
        port = 0
    if 1 <= port <= 65535:
        return port, False
    state.ui.say(
        "The existing Server port is invalid. Using 8000 as the editable default.",
        "已有 Server 端口无效，将以 8000 作为可修改的默认值。",
    )
    return 8000, True


def _custom_access(state: Wizard, port: int) -> tuple[str, int, str]:
    """Return the custom bind host, port, and client-visible URL."""
    stored_host = state.values.get(f"{SERVER}HTTP_HOST", "127.0.0.1")
    default_host = "0.0.0.0" if stored_host in {"127.0.0.1", "localhost", "::1"} else stored_host  # noqa: S104
    host = state.ui.ask(
        "Server bind address (not the client URL)",
        "Server 监听地址（不是客户端 URL）",
        default=default_host,
        required=True,
    )
    port = state.ui.integer("Server port", "Server 端口", default=port, maximum=65535)
    state.ui.say(
        "The built-in Server provides HTTP only. The client URL below must be the HTTPS address exposed by your "
        "existing proxy, gateway, or load balancer.",
        "内置 Server 只提供 HTTP。下面的客户端 URL 必须填写现有反向代理、网关或负载均衡对外提供的 HTTPS 地址。",
    )
    address = _server_url(state.ui, state.values.get(f"{SERVER}PUBLIC_URL", ""))
    state.patch({f"{SERVER}PUBLIC_URL": address})
    state.note(
        "PowerContext does not provide HTTPS itself; configure TLS termination for the client-visible URL separately.",
        "PowerContext 本身不提供 HTTPS；客户端访问地址所需的 TLS 终止必须单独配置。",
    )
    return host, port, address


def _reverse_proxy_access(state: Wizard, port: int) -> tuple[str, int, str]:
    """Keep a loopback listener and return the public HTTPS URL."""
    state.ui.say(
        "PowerContext will stay on loopback behind an HTTPS proxy such as Nginx or Caddy.",
        "PowerContext 将监听环回地址，并由 Nginx、Caddy 等 HTTPS 反向代理对外提供服务。",
    )
    port = state.ui.integer("Server port behind the proxy", "反向代理后的 Server 端口", default=port, maximum=65535)
    address = _server_url(state.ui, state.values.get(f"{SERVER}PUBLIC_URL", ""))
    state.patch({f"{SERVER}PUBLIC_URL": address})
    state.note(
        "Configure the HTTPS proxy separately; this wizard does not install certificates or proxies.",
        "HTTPS 代理需要单独配置；本向导不会安装证书或代理。",
    )
    return "127.0.0.1", port, address


def _ssh_forwarding_access(state: Wizard, port: int, dashboard: bool) -> tuple[str, int, str]:
    """Keep loopback and record a tunnel command that runs on the client."""
    remote_host = state.ui.ask(
        "SSH host or alias (for instructions)", "SSH 主机或别名（用于生成说明）", default="t1", required=True
    )
    local_port = state.ui.integer(
        "Forwarded port on your other computer", "另一台电脑上的转发端口", default=18000, maximum=65535
    )
    state.forwarded_address = f"http://127.0.0.1:{local_port}"
    state.ssh_tunnel_command = f"ssh -N -L {local_port}:127.0.0.1:{port} {shlex.quote(remote_host)}"
    state.note(
        f"Run on the client computer: {state.ssh_tunnel_command}",
        f"在客户端电脑执行：{state.ssh_tunnel_command}",
    )
    if dashboard:
        state.note(
            f"Browser after forwarding: http://127.0.0.1:{local_port}/dashboard/home",
            f"转发后的浏览器入口：http://127.0.0.1:{local_port}/dashboard/home",
        )
    state.patch({f"{SERVER}PUBLIC_URL": None})
    return "127.0.0.1", port, f"http://127.0.0.1:{port}"


def _network(state: Wizard) -> None:
    ui = state.ui
    ui.section("4. Dashboard and access", "4. Dashboard 与访问")
    dashboard = ui.confirm(
        "Enable the browser Dashboard? This also enables authenticated access and creates or retains a Server token.",
        "开启浏览器 Dashboard？这会同时启用访问认证，并生成或沿用 Server Token。",
        default=state.values.get(f"{SERVER}DASHBOARD_ENABLED", "true") == "true",
    )
    port, invalid_port = _stored_network_port(state)
    state.forwarded_address = ""
    state.ssh_tunnel_command = ""
    if state.scenario == "local" and (invalid_port or port != 8000):
        port = ui.integer("Server port", "Server 端口", default=port, maximum=65535)
    host = "127.0.0.1"
    address = f"http://127.0.0.1:{port}"
    if state.scenario != "local":
        access = ui.choose(
            "Remote access method",
            "远程访问方式",
            [
                ("custom", "Custom listener address and client URL", "自定义监听地址和客户端 URL"),
                (
                    "https",
                    "HTTPS reverse proxy, such as Nginx or Caddy",
                    "使用 HTTPS 反向代理（如 Nginx、Caddy）",
                ),
                (
                    "ssh",
                    "SSH port forwarding (run the generated command on the client)",
                    "SSH 端口转发（需在客户端执行生成的命令）",
                ),
            ],
            default="custom",
        )
        if access == "ssh":
            if invalid_port:
                port = ui.integer("Server port", "Server 端口", default=port, maximum=65535)
                address = f"http://127.0.0.1:{port}"
            host, port, address = _ssh_forwarding_access(state, port, dashboard)
        elif access == "https":
            host, port, address = _reverse_proxy_access(state, port)
        else:
            host, port, address = _custom_access(state, port)
    token = state.values.get(f"{SERVER}AUTH_TOKEN", "")
    authenticated = (
        dashboard or state.scenario != "local" or bool(token) or state.values.get(f"{SERVER}ACCESS_MODE") == "enforced"
    )
    if authenticated and not token:
        token = secrets.token_urlsafe(32)
        state.generated_auth_token = token
        ui.say(
            "A private Server token will be generated and shown once after the files are saved.",
            "将生成私有 Server Token，并在文件保存成功后显示一次。",
        )
    else:
        state.generated_auth_token = ""
    updates: dict[str, str | None] = {
        f"{SERVER}HTTP_HOST": host,
        f"{SERVER}HTTP_PORT": str(port),
        f"{SERVER}DASHBOARD_ENABLED": str(dashboard).lower(),
        f"{SERVER}MCP_ENABLED": "true",
        f"{SERVER}MCP_PATH": "/mcp",
        f"{SERVER}ACCESS_MODE": "enforced" if authenticated else "disabled",
    }
    if authenticated:
        updates[f"{SERVER}AUTH_TOKEN"] = token
    state.patch(updates)
    state.client = {f"{CLIENT}SERVER_URL": address}
    if token:
        state.client[f"{CLIENT}API_TOKEN"] = token
    if dashboard:
        dashboard_address = state.forwarded_address or address
        state.note(
            f"Dashboard: {dashboard_address}/dashboard/home",
            f"Dashboard：{dashboard_address}/dashboard/home",
        )


def _infer_features(state: Wizard) -> None:
    for family in ("memory", "topic-memory", "experience"):
        if state.values.get(f"{RUNTIME}{family.upper().replace('-', '_')}_SCHEDULE_SECONDS"):
            state.features.add(family)
    if state.values.get(f"{RUNTIME}SCHEDULE_SECONDS"):
        state.features.add("memory")
    if state.values.get(f"{RUNTIME}PROFILE_SCHEDULE_ENABLED") == "true":
        state.features.add("profile")
    if state.values.get(f"{INFERENCE}EMBEDDING_MODEL"):
        state.features.add("vector")
    if state.values.get(f"{RUNTIME}MEMORY_RERANK_ENABLED") == "true":
        state.features.add("rerank")
    if state.values.get(f"{INFERENCE}GENERATION_MODEL"):
        state.features.add("skill")


def _models(state: Wizard, required_features: set[str] | None = None) -> None:
    selected = state.features if required_features is None else required_features
    generation = bool(selected & {"memory", "topic-memory", "experience", "profile", "skill"})
    embedding = "vector" in selected
    rerank = "rerank" in selected
    if generation or embedding or rerank:
        state.patch(
            collect_models(
                state.ui,
                state.values,
                generation=generation,
                embedding=embedding,
                rerank=rerank,
            )
        )


def _missing_model_features(state: Wizard, newly_enabled: set[str]) -> set[str]:
    """Return only newly enabled features whose model connection is incomplete."""
    missing: set[str] = set()
    generation_features = newly_enabled & {"memory", "topic-memory", "experience", "profile", "skill"}
    if generation_features and not state.values.get(f"{INFERENCE}GENERATION_MODEL"):
        missing.update(generation_features)
    embedding_fields = ("EMBEDDING_MODEL", "EMBEDDING_PROFILE_ID", "EMBEDDING_DIMENSION")
    if "vector" in newly_enabled and not all(state.values.get(f"{INFERENCE}{suffix}") for suffix in embedding_fields):
        missing.add("vector")
    if "rerank" in newly_enabled:
        missing.add("rerank")
    return missing


def _processing(state: Wizard) -> None:
    automatic = state.features & {"memory", "topic-memory", "experience", "profile"}
    if not automatic:
        return
    ui = state.ui
    ui.section("6. Background processing", "6. 后台处理")
    names = ", ".join(sorted(automatic))
    ui.say(
        f"Automatic processing is already enabled for: {names}. Now choose how to set its schedule.",
        f"已启用自动处理：{names}。下面只选择处理周期，不会关闭这些能力。",
    )
    schedule_mode = ui.choose(
        "How should processing schedules be configured?",
        "如何设置自动处理周期？",
        [
            (
                "recommended",
                _recommended_schedule_summary(automatic, "en"),
                _recommended_schedule_summary(automatic, "zh"),
            ),
            ("custom", "Customize each schedule", "逐项自定义周期"),
        ],
        default="recommended",
    )
    recommended = schedule_mode == "recommended"
    for family in sorted(automatic - {"profile"}):
        key = f"{RUNTIME}{family.upper().replace('-', '_')}_SCHEDULE_SECONDS"
        family_recommended = RECOMMENDED_PROCESSING_SCHEDULE_SECONDS[family]
        default = int(float(state.values.get(key, str(family_recommended))))
        seconds = (
            family_recommended
            if recommended
            else ui.integer(f"{family}: check interval (seconds)", f"{family}：检查间隔（秒）", default=default)
        )
        state.patch({key: str(seconds)})
    if "profile" in automatic:
        cron = (
            RECOMMENDED_PROFILE_CRON
            if recommended
            else state.values.get(f"{RUNTIME}PROFILE_CRON", RECOMMENDED_PROFILE_CRON)
        )
        timezone = (
            RECOMMENDED_PROFILE_TIMEZONE
            if recommended
            else state.values.get(f"{RUNTIME}PROFILE_TIMEZONE", RECOMMENDED_PROFILE_TIMEZONE)
        )
        if not recommended:
            cron = ui.ask("Profile cron", "Profile 定时表达式", default=cron, required=True)
            timezone = ui.ask("Profile timezone", "Profile 时区", default=timezone, required=True)
        state.patch({f"{RUNTIME}PROFILE_CRON": cron, f"{RUNTIME}PROFILE_TIMEZONE": timezone})
        state.note(
            f"Profile schedule: {cron} ({timezone}). Enable the target Scope's Profile Policy after startup.",
            f"Profile 调度：{cron}（{timezone}）。启动后还需启用目标 Scope 的 Profile Policy。",
        )
    state.note(
        "Intervals are checks, not completion deadlines. Generation and Worker timeouts are independent.",
        "间隔表示检查频率，不是完成时限；模型超时和 Worker 总超时独立设置。",
    )
    state.note(
        "Memory/Topic/Experience process all eligible Scopes; a client Scope binding does not limit scheduling.",
        "Memory/Topic/Experience 会处理所有符合条件的 Scope；客户端绑定不能限定后台扫描范围。",
    )
    if "experience" in state.features:
        state.note(
            "Experience requires task-outcome Sources and creates candidates for review, not automatic approval.",
            "Experience 需要 task-outcome 任务结果，只生成待审候选，不会自动批准。",
        )


def _recommended_schedule_summary(automatic: set[str], language: str) -> str:
    names = {"memory": "Memory", "topic-memory": "Topic Memory", "experience": "Experience"}
    parts: list[str] = []
    for family in ("memory", "topic-memory", "experience"):
        if family not in automatic:
            continue
        seconds = RECOMMENDED_PROCESSING_SCHEDULE_SECONDS[family]
        if language == "zh":
            duration = f"{seconds // 60} 分钟" if seconds % 60 == 0 else f"{seconds} 秒"
            parts.append(f"{names[family]} 每 {duration}")
        else:
            if seconds % 60 == 0:
                minutes = seconds // 60
                duration = f"{minutes} minute" + ("s" if minutes != 1 else "")
            else:
                duration = f"{seconds} seconds"
            parts.append(f"{names[family]} every {duration}")
    if "profile" in automatic:
        parts.append("Profile 每天 02:00" if language == "zh" else "Profile daily at 02:00")
    joined = "；".join(parts) if language == "zh" else "; ".join(parts)
    if language == "zh":
        return f"使用各 Artifact 推荐周期（{joined}）"
    return f"Use Artifact-specific recommended schedules ({joined})"


def _advanced(state: Wizard) -> None:
    ui = state.ui
    ui.section("Advanced settings", "高级设置")
    settings = (
        (f"{INFERENCE}GENERATION_TIMEOUT_SECONDS", "Generation timeout (seconds)", "单次 Generation 超时（秒）", 30),
        (f"{INFERENCE}GENERATION_MAX_REQUESTS", "Generation request limit", "单次 Generation 请求上限", 2),
        (
            f"{INFERENCE}GENERATION_MODEL_CONTEXT_WINDOW_TOKENS",
            "Model context window (tokens)",
            "模型上下文窗口（token）",
            125000,
        ),
        (f"{INFERENCE}EMBEDDING_TIMEOUT_SECONDS", "Embedding timeout (seconds)", "Embedding 请求超时（秒）", 30),
        (f"{INFERENCE}EMBEDDING_BATCH_SIZE", "Embedding batch size", "Embedding 批量大小", 10),
    )
    for key, en, zh, default in settings:
        if ("GENERATION" in key and not state.values.get(f"{INFERENCE}GENERATION_MODEL")) or (
            "EMBEDDING" in key and not state.values.get(f"{INFERENCE}EMBEDDING_MODEL")
        ):
            continue
        state.patch({key: str(ui.integer(en, zh, default=int(float(state.values.get(key, str(default))))))})
    for family in sorted(state.features & {"memory", "topic-memory", "experience", "profile"}):
        prefix = f"{RUNTIME}{family.upper().replace('-', '_')}_"
        for suffix, en, zh, default in (
            ("MAX_WORKERS", "worker concurrency", "并发数", 1),
            ("WORKER_TIMEOUT_SECONDS", "total worker timeout (seconds)", "Worker 总超时（秒）", 600),
        ):
            key = prefix + suffix
            state.patch({
                key: str(
                    ui.integer(
                        f"{family} {en}", f"{family} {zh}", default=int(float(state.values.get(key, str(default))))
                    )
                )
            })
    level = ui.choose(
        "Log level",
        "日志级别",
        [(name, name, name) for name in ("INFO", "DEBUG", "WARNING", "ERROR")],
        default=state.values.get(f"{SERVER}LOGGING_LEVEL", "INFO"),
    )
    state.patch({f"{SERVER}LOGGING_LEVEL": level})


def _agents(state: Wizard) -> None:
    ui = state.ui
    ui.section("7. Agent connection files", "7. Agent 连接文件")
    if not state.client:
        port = state.values.get(f"{SERVER}HTTP_PORT", "8000")
        state.client[f"{CLIENT}SERVER_URL"] = state.values.get(f"{CLIENT}SERVER_URL", f"http://127.0.0.1:{port}")
        token = state.values.get(f"{SERVER}AUTH_TOKEN", state.values.get(f"{CLIENT}API_TOKEN", ""))
        if token:
            state.client[f"{CLIENT}API_TOKEN"] = token
    remaining = list(AGENT_SPECS)
    while remaining:
        choices = [(agent.identifier, agent.en, agent.zh) for agent in remaining]
        choices.append(("none", "Finish Agent configuration", "结束 Agent 配置"))
        selected = ui.choose(
            "Select an Agent to configure (one at a time)",
            "请选择一个要配置的 Agent（每次配置一个）",
            choices,
            default="none" if state.agents else preferred_agent(remaining),
        )
        if selected == "none":
            if not state.agents and not ui.confirm(
                "No Agent connection configuration will be generated. Continue without an Agent?",
                "不会生成任何 Agent 连接配置。确认不配置 Agent 并继续吗？",
                default=False,
            ):
                continue
            break
        agent = AGENT_SPEC_BY_ID[selected]
        _configure_agent(state, agent)
        remaining.remove(agent)
        state.agents = (*state.agents, selected)
    if not state.agents:
        return
    state.note(
        "Each Agent can use a different Scope. A new Scope must be created by Server before its returned ID can be bound.",
        "每个 Agent 可以使用不同 Scope；新 Scope 必须由 Server 创建后，才能绑定其返回的 ID。",
    )
    if "claude-code" in state.agents:
        state.note(
            "Claude capture records user prompts, not its final replies. Verify both Hook and MCP authentication.",
            "Claude 当前采集用户输入，不采集最终回复；需同时验证 Hook 与 MCP 认证。",
        )
    if "codex" in state.agents:
        state.note(
            "Codex MCP URL must match this client URL; desktop apps may not inherit your terminal environment.",
            "Codex MCP 地址必须与客户端 URL 一致；桌面 App 可能不继承终端环境。",
        )


def _configure_agent(state: Wizard, agent: AgentSpec) -> None:
    ui = state.ui
    address = state.client[f"{CLIENT}SERVER_URL"]
    if state.forwarded_address:
        location = ui.choose(
            "Where will the Agent run?",
            "Agent 将运行在哪台机器？",
            [
                ("server", "On the Server machine", "Server 所在机器"),
                ("other", "On the other computer through SSH forwarding", "另一台使用 SSH 转发的电脑"),
            ],
            default="server",
        )
        if location == "other":
            address = state.forwarded_address
        else:
            address = f"http://127.0.0.1:{state.values.get(f'{SERVER}HTTP_PORT', '8000')}"
    if state.scenario != "local":
        address = _server_url(ui, address)
    if not state.agent_addresses or agent.identifier == "codex":
        state.client[f"{CLIENT}SERVER_URL"] = address
    capture = True
    if state.advanced:
        capture = ui.confirm(
            "Capture ordinary prompts or turns as Sources?",
            "将普通提示词或对话轮次采集为 Source？",
            default=True,
        )
    else:
        if state.features & {"memory", "topic-memory"}:
            ui.say(
                "Ordinary prompts or turns will be captured as Source evidence for automatic memory processing.",
                "将采集普通提示词或对话轮次作为 Source 证据，供自动记忆处理使用。",
            )
        else:
            ui.say(
                "Ordinary prompts or turns will be captured as Source evidence. Basic memory still requires explicit "
                "saves; capture alone does not extract Memory.",
                "将采集普通提示词或对话轮次作为 Source 证据。基础记忆仍需显式保存；仅采集不会自动提取 Memory。",
            )
    if not capture and state.features & {"memory", "topic-memory"}:
        state.note(
            f"{agent.en} Source capture is disabled; its ordinary conversations will not drive automatic Memory or "
            "Topic Memory.",
            f"{agent.zh} 已关闭 Source 采集；该 Agent 的普通对话不会推动自动 Memory 或 Topic Memory。",
        )
    scope_mode = ui.choose(
        "Which Scope should this Agent use?",
        "这个 Agent 应使用哪个 Scope？",
        [
            (
                "new",
                "Plan a new isolated Scope (create it later from the saved instructions)",
                "规划新的独立 Scope（保存后按指引创建）",
            ),
            ("existing", "Bind a Scope ID I already have", "绑定我已有的 Scope ID"),
            (
                "default",
                "Leave unbound; use session/workspace binding, then the Server default",
                "暂不固定 Scope（使用会话/工作区绑定，再回落 Server 默认值）",
            ),
        ],
        default="new",
    )
    scope = ""
    if scope_mode == "existing":
        scope = ui.ask("Existing Scope ID", "已有 Scope ID", required=True)
    elif scope_mode == "new":
        state.planned_scopes[agent.identifier] = f"{agent.identifier}-{uuid.uuid4().hex[:8]}"
        ui.say(
            f"Planned isolated Scope title: {state.planned_scopes[agent.identifier]}. "
            "The Server will return the real Scope ID.",
            f"计划创建独立 Scope：{state.planned_scopes[agent.identifier]}。真实 Scope ID 由 Server 返回。",
        )
    _agent_fields(state, agent, address, capture=capture, scope=scope)


def _agent_fields(state: Wizard, agent: AgentSpec, address: str, *, capture: bool, scope: str) -> None:
    state.agent_addresses[agent.identifier] = address
    prefix = agent.environment_prefix
    if prefix is None:
        settings: dict[str, object] = {agent.server_setting or "endpoint": address, agent.capture_setting: capture}
        if scope:
            settings[agent.scope_setting] = scope
        if "profile" in state.features:
            settings[agent.context_assembly_setting] = _context_assembly(state)
        state.agent_settings[agent.identifier] = settings
        return
    if server_name := agent.environment_name(agent.server_setting):
        state.client[server_name] = address
    capture_name = agent.environment_name(agent.capture_setting)
    if capture_name is None:
        message = f"missing capture environment name for {agent.identifier}"
        raise RuntimeError(message)
    state.client[capture_name] = str(capture).lower()
    if scope and (scope_name := agent.environment_name(agent.scope_setting)):
        state.client[scope_name] = scope
    token = state.client.get(f"{CLIENT}API_TOKEN")
    if token and (authorization_name := agent.environment_name(agent.authorization_setting)):
        state.client[authorization_name] = f"Bearer {token}"
    if "profile" in state.features and (assembly_name := agent.environment_name(agent.context_assembly_setting)):
        state.client[assembly_name] = json.dumps(_context_assembly(state))


def _context_assembly(state: Wizard) -> dict[str, object]:
    sections = [{"family": "memory", "limit": 3}, {"family": "profile", "limit": 1}]
    if "topic-memory" in state.features:
        sections.append({"family": "topic-memory", "limit": 2})
    if "experience" in state.features:
        sections.append({"family": "experience", "limit": 2})
    return {"sections": sections}


def _client_updates(state: Wizard) -> dict[str, str | None]:
    updates: dict[str, str | None] = {f"{CLIENT}API_TOKEN": None}
    for agent in state.agents:
        spec = AGENT_SPEC_BY_ID[agent]
        for setting in (spec.scope_setting, spec.authorization_setting, spec.context_assembly_setting):
            if name := spec.environment_name(setting):
                updates[name] = None
    updates.update(state.client)
    return updates


def _edit_modules(state: Wizard) -> None:
    _infer_features(state)
    while True:
        module = state.ui.choose(
            "Choose a module to edit",
            "选择要修改的模块",
            [
                ("network", "Dashboard and access", "Dashboard 与访问"),
                ("capabilities", "Memory capabilities", "记忆能力"),
                ("models", "Model connections", "模型连接"),
                ("processing", "Background processing schedules", "后台处理周期"),
                ("agent", "Agent connections", "Agent 连接"),
                ("advanced", "Advanced limits and logging", "高级限制与日志"),
                ("done", "Review and save", "查看并保存"),
            ],
            default="done",
        )
        if module == "done":
            return
        if module == "network":
            _scenario(state)
            _network(state)
        elif module == "capabilities":
            old_features = set(state.features)
            _capabilities(state)
            if missing := _missing_model_features(state, state.features - old_features):
                _models(state, missing)
        elif module == "models":
            _models(state)
        elif module == "processing":
            _processing(state)
        elif module == "agent":
            _agents(state)
        else:
            _advanced(state)


def _preview(state: Wizard, before: dict[str, str], after: dict[str, str]) -> None:
    from powercontext.cli.config import _is_secret_name

    for key in sorted(before.keys() | after.keys()):
        if before.get(key) == after.get(key):
            continue
        marker = "~" if key in before and key in after else "+" if key in after else "-"
        value = after.get(key, state.ui.text("removed", "移除"))
        if _is_secret_name(key) or key.endswith("_MODEL_SETTINGS"):
            value = state.ui.text("<hidden>", "<已隐藏>")
        typer.echo(f"  {marker} {key}={value}")


def _validate_files(state: Wizard, values: dict[str, str]) -> None:
    from powercontext.builtin.runtime.composition import BuiltinConfigurationError
    from powercontext.builtin.runtime.topic_memory_processing import validate_topic_memory_provider_settings
    from powercontext.cli.config import (
        ConfigError,
        _server_settings_from_environment,
        _temporary_environment,
        _validate_builtin_runtime,
        _validate_server_settings,
    )

    try:
        _validate_server_settings(values)
        _validate_builtin_runtime(values)
        managed = {name for name in os.environ if name.startswith(SERVER)}
        with _temporary_environment(values, clear=managed):
            settings = _server_settings_from_environment()
        if settings.runtime.topic_memory_schedule_seconds is not None:
            validate_topic_memory_provider_settings(settings.inference)
    except ValidationError as error:
        for item in error.errors(include_input=False, include_context=False, include_url=False):
            field_name = ".".join(map(str, item["loc"])) or "server"
            state.ui.say(
                f"Invalid setting: {field_name} ({item['type']})", f"配置项无效：{field_name}（{item['type']}）"
            )
        message = "Settings are invalid; nothing was written / 配置校验未通过，未写入文件"
        raise WizardInputError(message) from None
    except (ConfigError, BuiltinConfigurationError):
        message = (
            "Model/processing configuration is incompatible; check provider, credentials and model settings. "
            "Nothing was written / 模型或处理配置不兼容，请检查服务商、凭据和模型参数。未写入文件"
        )
        raise WizardInputError(message) from None


def _finish(state: Wizard, output: Path, original_content: str) -> None:

    ui = state.ui
    ui.section("8. Review files", "8. 检查配置文件")
    updates = _client_updates(state) if state.client_only else state.updates
    updated = update_document(original_content, updates, language=ui.language)
    values = parse_environment(updated)
    _preview(state, state.original, values)
    if not state.client_only:
        _validate_files(state, values)
        ui.say(
            "Static configuration passed. Database/model connectivity and memory processing remain unverified.",
            "静态配置校验通过；尚未验证数据库/模型连通性及实际记忆处理。",
        )
        conflicting = sorted(
            key
            for key, value in values.items()
            if key in os.environ and os.environ[key] != value and (key.startswith(SERVER) or key == "POWERCONTEXT_HOME")
        )
        if conflicting:
            state.note(
                "Process environment overrides this file: " + ", ".join(conflicting),
                "进程环境会覆盖此文件中的以下配置：" + "、".join(conflicting),
            )
        if state.storage_summary.get("status") == "existing" and any(
            key.startswith((INFERENCE, RUNTIME)) for key in state.updates
        ):
            state.note(
                "Existing data: check processing/index compatibility and maintenance requirements before restart.",
                "已有数据：重启前请检查处理能力、索引兼容性及所需维护。",
            )
    if state.agents and not state.client_only:
        updated = update_document(updated, _client_updates(state), language=ui.language)
    bundle = {output: updated}
    snapshots = {output: original_content}
    client_file = output
    if state.agents and not state.client_only:
        ui.say(
            "Agent connection settings are included in the same environment file:",
            "Agent 连接配置已合并到同一个环境文件：",
        )
        _preview(state, values, parse_environment(updated))
    notes_file = output.with_name(output.name + ".next-steps.md")
    _check_output(notes_file)
    snapshots[notes_file] = notes_file.read_text(encoding="utf-8") if notes_file.exists() else ""
    bundle[notes_file] = _next_steps(state, output, client_file)
    for path in bundle:
        typer.echo(f"  {path}")
    if not ui.confirm("Save these files?", "保存这些文件？"):
        ui.say("No changes written.", "未写入任何文件。")
        return
    _save_bundle(state, bundle, snapshots)
    _finish_seekdb_install(state)
    _show_connection_details(state, output, values, notes_file)


def _save_bundle(state: Wizard, bundle: dict[Path, str], snapshots: dict[Path, str]) -> None:
    from powercontext.cli.config import write_environment

    ui = state.ui
    for path, previous in snapshots.items():
        _check_output(path)
        current = path.read_text(encoding="utf-8") if path.exists() else ""
        if current != previous:
            message = "Output changed during the wizard; run it again / 输出文件已被他人修改，请重新运行"
            raise WizardInputError(message)
    for path, body in bundle.items():
        backup = write_environment(path, body, backup=path.exists())
        ui.say(f"Saved: {path}", f"已保存：{path}")
        if backup:
            ui.say(f"Backup: {backup}", f"备份：{backup}")
    ui.say(
        "Files are ready. Follow the next-steps document to start and verify the service.",
        "文件已生成。请按后续步骤文档启动并验收服务。",
    )


def _finish_seekdb_install(state: Wizard) -> None:
    task = state.seekdb_install_task
    if task is None:
        return
    if not task.done():
        state.ui.section("Finishing seekdb dependency installation", "正在完成 seekdb 依赖安装")
        state.ui.wait_for_activity(task.done, task.phase, task.elapsed_seconds)
    result = task.wait()
    if result.status == "ready":
        state.ui.say(
            f"seekdb dependency installation completed, version {result.version}.",
            f"seekdb 依赖安装完成，版本 {result.version}。",
        )
        return
    if result.status == "unsupported":
        state.ui.say(
            "No compatible seekdb package is available for this Python and platform. Choose SQLite or OceanBase instead.",
            "当前 Python 与平台没有兼容的 seekdb 安装包，请改用 SQLite 或 OceanBase。",
        )
        return
    state.ui.say(
        f"seekdb dependency installation failed: {result.reason}.",
        f"seekdb 依赖安装失败：{result.reason}。",
    )
    if result.manual_command:
        state.ui.say("Install it manually before starting Server:", "启动 Server 前请手动执行：")
        typer.echo(result.manual_command)


def _show_connection_details(state: Wizard, output: Path, values: dict[str, str], notes_file: Path) -> None:
    """Print the URLs and commands that need immediate user attention after a successful save."""

    ui = state.ui
    ui.section("Connection details", "连接与下一步")
    if not state.client_only:
        command = f"powercontext server run --env-file {shlex.quote(str(output))}"
        ui.say(f"Start Server: {command}", f"启动 Server：{command}")
    if state.ssh_tunnel_command:
        ui.say(
            f"Run on the client computer: {state.ssh_tunnel_command}",
            f"在客户端电脑执行 SSH 隧道：{state.ssh_tunnel_command}",
        )
    base_url = _connection_base_url(state, values)
    if values.get(f"{SERVER}DASHBOARD_ENABLED") == "true" and base_url:
        ui.say(f"Dashboard: {base_url}/dashboard/home", f"Dashboard：{base_url}/dashboard/home")
    if values.get(f"{SERVER}MCP_ENABLED") == "true" and base_url:
        path = values.get(f"{SERVER}MCP_PATH", "/mcp")
        ui.say(f"MCP endpoint: {base_url}{path}", f"MCP 地址：{base_url}{path}")
    token = values.get(f"{SERVER}AUTH_TOKEN", "")
    if state.generated_auth_token:
        ui.say(
            "Server token (shown only this time; do not paste it into logs, screenshots, or issues):",
            "Server Token（仅本次显示；请勿粘贴到日志、截图或 Issue）：",
        )
        typer.echo(state.generated_auth_token)
    if token:
        ui.say(
            f"Later, find POWERCONTEXT_SERVER_AUTH_TOKEN in {output}",
            f"以后可在 {output} 中查找 POWERCONTEXT_SERVER_AUTH_TOKEN",
        )
    ui.say(f"Full instructions: {notes_file}", f"完整操作说明：{notes_file}")


def _connection_base_url(state: Wizard, values: dict[str, str]) -> str:
    if state.forwarded_address:
        return state.forwarded_address.rstrip("/")
    if address := state.client.get(f"{CLIENT}SERVER_URL"):
        return address.rstrip("/")
    if address := values.get(f"{SERVER}PUBLIC_URL"):
        return address.rstrip("/")
    host = values.get(f"{SERVER}HTTP_HOST", "127.0.0.1")
    if host in {"0.0.0.0", "::", "[::]"}:  # noqa: S104 - normalize wildcard listeners for a local browser URL
        host = "127.0.0.1"
    port = values.get(f"{SERVER}HTTP_PORT", "8000")
    return f"http://{host}:{port}"


def _next_steps(state: Wizard, output: Path, client_file: Path) -> str:
    ui = state.ui
    lines = [
        "# " + ui.text("PowerContext next steps", "PowerContext 后续步骤"),
        "",
        ui.text(
            "Only files were generated. No Server, database migration, Scope, or plugin was created.",
            "本次只生成文件，没有启动 Server、执行数据库迁移、创建 Scope 或安装插件。",
        ),
        "",
    ]
    if not state.client_only:
        lines += [
            "```bash",
            f"powercontext config validate --env-file {shlex.quote(str(output))}",
            f"powercontext server run --env-file {shlex.quote(str(output))}",
            "```",
            "",
        ]
        if state.values.get(f"{SERVER}AUTH_TOKEN"):
            lines += [
                ui.text(
                    f"The Server token is stored as `POWERCONTEXT_SERVER_AUTH_TOKEN` in `{output}`. "
                    "Do not paste it into logs or issues.",
                    f"Server Token 保存在 `{output}` 的 `POWERCONTEXT_SERVER_AUTH_TOKEN` 中；"
                    "请勿将其粘贴到日志或 Issue。",
                ),
                "",
            ]
    if state.agents or state.client_only:
        if state.scenario != "local":
            lines += [
                ui.text(
                    "Run each Agent's commands on the computer where that Agent will run. If this is another "
                    "computer, copy the environment file there and adjust its path in the commands below.",
                    "请在各 Agent 实际运行的电脑上执行其对应命令。若为另一台电脑，先把环境文件复制过去，"
                    "并修改下方命令中的文件路径。",
                ),
                "",
            ]
        if state.ssh_tunnel_command:
            lines += [
                ui.text(
                    "On the other computer, start the SSH tunnel in a separate terminal and leave it running "
                    "before using the forwarded URLs:",
                    "在另一台电脑的独立终端中启动 SSH 隧道并保持运行，然后再使用转发后的地址：",
                ),
                "",
                "```bash",
                state.ssh_tunnel_command,
                "```",
                "",
            ]
        lines += [
            ui.text(
                "Load the environment file in the terminal that starts your Agent:",
                "在启动 Agent 的终端中，加载这个环境文件：",
            ),
            "",
            "```bash",
            "set -a",
            f". {shlex.quote(str(client_file))}",
            "set +a",
            "powercontext ready",
            "powercontext capabilities",
            "```",
            "",
        ]
        lines += _alternate_client_check_steps(state)
    if state.planned_scopes:
        lines += _scope_creation_steps(state)
        lines += [
            ui.text(
                "After saving the returned Scope IDs in the environment file, reload it in each Agent's "
                "terminal. Reload the edited client environment before starting a new Agent; editing a file "
                "does not update an already running process:",
                "将返回的 Scope ID 保存到环境文件后，在各 Agent 的终端中重新加载该文件，再启动新的 "
                "Agent。只编辑文件不会更新已经运行的进程：",
            ),
            "",
            "```bash",
            "set -a",
            f". {shlex.quote(str(client_file))}",
            "set +a",
            "```",
            "",
        ]
    lines += _agent_installation_steps(state)
    if "profile" in state.features:
        lines += _profile_policy_steps(state)
    lines += ["## " + ui.text("Checks and pending steps", "验收与待完成项"), ""]
    lines += ["- " + note for note in state.notes]
    lines += [
        "- "
        + ui.text(
            "Open a new Agent session, explicitly save a decision, then find its citation in another session.",
            "打开新 Agent 会话，显式保存一条决策，再在另一会话找到决策及其 citation。",
        )
    ]
    if "topic-memory" in state.features:
        lines += [
            "- "
            + ui.text(
                "For Topic Memory, confirm Source ingestion, Topic creation and evolution after a related second input.",
                "验收 Topic Memory 时，确认 Source 已入库、主题已生成，并在第二条相关输入后正确演进。",
            )
        ]
    return "\n".join(lines) + "\n"


def _alternate_client_check_steps(state: Wizard) -> list[str]:
    if len(set(state.agent_addresses.values())) < 2:
        return []
    lines = [
        state.ui.text(
            "The shared Client CLI URL follows Codex when configured, otherwise the first Agent. "
            "Each Agent retains its own endpoint. To check an Agent at another endpoint, run its checks "
            "on that Agent's computer with the URL explicitly selected:",
            "通用 Client CLI 地址采用 Codex 的地址；未配置 Codex 时采用首个 Agent 的地址。"
            "各 Agent 保留各自的连接地址。检查其他地址时，请在对应 Agent 的电脑上显式指定 URL：",
        ),
        "",
    ]
    for agent, address in state.agent_addresses.items():
        lines += [
            f"### {AGENT_SPEC_BY_ID[agent].en}",
            "",
            "```bash",
            f"POWERCONTEXT_CLIENT_SERVER_URL={shlex.quote(address)} powercontext ready",
            f"POWERCONTEXT_CLIENT_SERVER_URL={shlex.quote(address)} powercontext capabilities",
            "```",
            "",
        ]
    return lines


def _profile_policy_steps(state: Wizard) -> list[str]:
    ui = state.ui
    lines = [
        "## " + ui.text("Enable Profile for each Scope", "为各 Scope 开启 Profile"),
        "",
        ui.text(
            "After loading the client environment, run the matching command on each Agent's computer. "
            "It reads the current policy version before enabling generation with review_required. "
            "A missing policy (HTTP 404) is created with expected_version=0; any other failure stops the command. "
            "Profile candidates will need your review before activation.",
            "加载客户端环境后，在各 Agent 的电脑上执行对应命令。命令先读取策略的当前版本，再以 review_required "
            "开启生成；策略不存在（HTTP 404）时使用 expected_version=0 创建，其他错误会停止执行。"
            "生成的 Profile 候选需审核后才生效。",
        ),
        "",
    ]
    if not state.agents and state.values.get(f"{SERVER}AUTH_TOKEN"):
        lines += [
            ui.text(
                "Replace <server-token> with the Server token saved in your environment file before running the Profile command:",
                "执行 Profile 命令前，请将 <server-token> 替换为环境文件中保存的 Server Token：",
            ),
            "",
            "```bash",
            "export POWERCONTEXT_CLIENT_API_TOKEN='<server-token>'",
            "```",
            "",
        ]
    for identifier in state.agents or (None,):
        spec = AGENT_SPEC_BY_ID[identifier] if identifier else None
        scope_name = spec.environment_name(spec.scope_setting) if spec else None
        bound = scope_name and (scope_name in state.client or identifier in state.planned_scopes)
        if not bound:
            scope_name = "POWERCONTEXT_PROFILE_SCOPE_ID"
        address = state.agent_addresses[identifier] if identifier else _connection_base_url(state, state.values)
        authorization_name = spec.environment_name(spec.authorization_setting) if spec else None
        lines += ["### " + (spec.en if spec else "Scope"), ""]
        if not bound:
            lines += [
                ui.text(
                    "Replace <scope-id> with the real Scope ID used by this Agent or selected in Dashboard:",
                    "请将 <scope-id> 替换为该 Agent 使用的或 Dashboard 中选中的真实 Scope ID：",
                ),
                "",
                "```bash",
                "export POWERCONTEXT_PROFILE_SCOPE_ID='<scope-id>'",
                "```",
                "",
            ]
        lines += ["```bash", "python3 - <<'PY'"]
        lines += _profile_policy_script(address, scope_name or "POWERCONTEXT_PROFILE_SCOPE_ID", authorization_name)
        lines += ["PY", "```", ""]
    return lines


def _profile_policy_script(address: str, scope_name: str, authorization_name: str | None) -> list[str]:
    return dedent(f"""\
        import json
        import os
        import sys
        from urllib.error import HTTPError, URLError
        from urllib.parse import quote
        from urllib.request import Request, urlopen

        scope_id = os.environ.get({scope_name!r}, "").strip()
        if not scope_id or scope_id == "<scope-id>":
            sys.exit("Set {scope_name} to a real Scope ID and reload the client environment first.")
        address = {address!r}
        url = address.rstrip("/") + "/v1/scopes/" + quote(scope_id, safe="") + "/profile-policy"
        headers = {{"Accept": "application/json", "Content-Type": "application/json"}}
        authorization = os.environ.get({authorization_name or ""!r}, "")
        if not authorization and os.environ.get("POWERCONTEXT_CLIENT_API_TOKEN"):
            authorization = "Bearer " + os.environ["POWERCONTEXT_CLIENT_API_TOKEN"]
        if authorization:
            headers["Authorization"] = authorization
        try:
            with urlopen(Request(url, headers=headers), timeout=30) as response:
                current = json.load(response)
            version = current.get("version") if isinstance(current, dict) else None
            if type(version) is not int or version < 1:
                sys.exit("GET profile policy returned an invalid version; no policy was changed.")
        except HTTPError as error:
            error.close()
            if error.code != 404:
                sys.exit(f"GET profile policy failed (HTTP {{error.code}}); no policy was changed.")
            version = 0
        except (URLError, OSError, ValueError):
            sys.exit("GET profile policy failed; check connectivity and the Server. No policy was changed.")
        payload = json.dumps({{
            "generation_enabled": True,
            "activation_mode": "review_required",
            "expected_version": version,
        }}).encode()
        try:
            with urlopen(Request(url, data=payload, headers=headers, method="PUT"), timeout=30) as response:
                json.load(response)
        except HTTPError as error:
            error.close()
            sys.exit(f"PUT profile policy failed (HTTP {{error.code}}); resolve the error and rerun this command.")
        except (URLError, OSError, ValueError):
            sys.exit("Could not confirm the Profile policy update; check connectivity and rerun this command.")
        print("Profile generation enabled with review_required for Scope " + scope_id)
        """).splitlines()


def _agent_installation_steps(state: Wizard) -> list[str]:
    if not state.agents:
        return []
    source = installation_source(Path(__file__))
    lines = [
        state.ui.text(
            "Install the plugin from the same source as this PowerContext installation:",
            "从当前 PowerContext 安装使用的相同来源安装插件：",
        )
        if source is not None
        else state.ui.text(
            "The source of this installed package could not be identified. Choose the matching repository and branch "
            "or tag before installing your Agent plugin. Consult the setup options below:",
            "无法确定当前安装包的源码来源。安装 Agent 插件前，请选择与当前版本匹配的仓库及分支或标签。"
            "可通过以下命令查看安装选项：",
        ),
        "",
        "```bash",
    ]
    for agent in state.agents:
        spec = AGENT_SPEC_BY_ID[agent]
        if source is None:
            lines.append(f"powercontext setup {agent} --help")
            continue
        command = f"powercontext setup {agent} --source {shlex.quote(source.source)}"
        if source.ref:
            command += f" --ref {shlex.quote(source.ref)}"
        if spec.setup_server_url:
            command += f" --server-url {shlex.quote(state.agent_addresses[agent])}"
        lines += [command, f"powercontext doctor {agent}"]
    lines += ["```", ""]
    if "openclaw" in state.agents:
        lines += _openclaw_configuration_steps(state)
    if "codex" in state.agents:
        lines += _codex_endpoint_steps(state)
    return lines


def _scope_creation_steps(state: Wizard) -> list[str]:
    lines = [
        "## " + state.ui.text("Create the planned isolated Scopes", "创建计划中的独立 Scope"),
        "",
        state.ui.text(
            "POST /v1/scopes creates the Scope and returns its opaque scope_id. Run each request after Server starts, "
            "then use the returned scope_id in the matching client environment variable. Do not use the title as an ID.",
            "Server 启动后，逐个调用 POST /v1/scopes。响应会返回不透明的 scope_id；"
            "请把它写入对应客户端环境变量，不要把标题当作 ID。",
        ),
        "",
    ]
    for agent, title in state.planned_scopes.items():
        spec = AGENT_SPEC_BY_ID[agent]
        address = state.agent_addresses[agent]
        payload = json.dumps(
            {
                "title": title,
                "summary": f"Isolated memory scope for {agent}",
                "idempotency_key": title,
            },
            ensure_ascii=False,
        )
        lines += [
            f"### {title}",
            "",
            "```bash",
            f"curl -sS -X POST {shlex.quote(address + '/v1/scopes')} \\",
            "  -H 'Content-Type: application/json' \\",
        ]
        authorization_name = spec.environment_name(spec.authorization_setting)
        if authorization_name and authorization_name in state.client:
            lines += [f'  -H "Authorization: ${authorization_name}" \\']
        elif spec.identifier == "openclaw" and f"{CLIENT}API_TOKEN" in state.client:
            lines += [f'  -H "Authorization: Bearer ${CLIENT}API_TOKEN" \\']
        lines += [
            f"  --data {shlex.quote(payload)}",
            "```",
            "",
            _scope_binding_instruction(state, spec),
            "",
        ]
    return lines


def _scope_binding_instruction(state: Wizard, spec: AgentSpec) -> str:
    if spec.identifier == "openclaw":
        return state.ui.text(
            "Then replace <returned-scope-id> and run: openclaw config set "
            "plugins.entries.memory-powercontext.config.scopeId '<returned-scope-id>'.",
            "然后把 <returned-scope-id> 替换为返回值并执行：openclaw config set "
            "plugins.entries.memory-powercontext.config.scopeId '<returned-scope-id>'。",
        )
    scope_name = spec.environment_name(spec.scope_setting)
    if scope_name is None:
        message = f"missing Scope configuration for {spec.identifier}"
        raise RuntimeError(message)
    return state.ui.text(
        f"Then set {scope_name} to the returned scope_id in the environment file.",
        f"然后把返回的 scope_id 写入环境文件的 {scope_name}。",
    )


def _openclaw_configuration_steps(state: Wizard) -> list[str]:
    settings = state.agent_settings["openclaw"]
    lines = [state.ui.text("Configure the OpenClaw plugin:", "配置 OpenClaw 插件："), "", "```bash"]
    for setting in ("endpoint", "autoCapture", "contextAssembly"):
        if setting not in settings:
            continue
        value = settings[setting]
        rendered = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else shlex.quote(value)
        lines.append(f"openclaw config set plugins.entries.memory-powercontext.config.{setting} {rendered}")
    lines += ["```", ""]
    return lines


def _codex_endpoint_steps(state: Wizard) -> list[str]:
    address = state.agent_addresses["codex"]
    configuration = {
        "mcpServers": {
            "powercontext": {
                "type": "http",
                "url": address + "/mcp",
                "required": False,
                "env_http_headers": {"Authorization": "POWERCONTEXT_CODEX_AUTHORIZATION"},
            },
        },
    }
    return [
        state.ui.text(
            "Codex: open the installed PowerContext plugin's .mcp.json. "
            "set mcpServers.powercontext to the following connection. Keep other servers unchanged, "
            "then restart Codex after loading the client environment. setup codex alone does not change this URL.",
            "Codex：打开已安装 PowerContext 插件目录中的 .mcp.json，将 mcpServers.powercontext "
            "连接改为下方内容，保留其他服务器配置。加载客户端环境后重启 Codex。"
            "单独执行 setup codex 不会替你修改此 URL。",
        ),
        "",
        "```json",
        json.dumps(configuration, indent=2, ensure_ascii=False),
        "```",
        "",
    ]
