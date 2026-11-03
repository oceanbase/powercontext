#
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

"""Render paired blog diagrams from the article text; no source screenshots are altered.

Run with python scripts/generate_blog_localized_figures.py.
"""

# ruff: noqa: RUF001
import unicodedata
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIGURES = []


def add(folder, name, titles, cards, footers=("", "")):
    FIGURES.append((folder, name, titles, cards, footers))


D = "deepseek-harness-powercontext"
# Each tuple pairs Chinese and English; content and geometry are shared.
add(
    D,
    "cordis-plugin-tree",
    ("能力通过插件组合", "Capabilities compose through plugins"),
    [
        (("Harness", "Harness"), ("Cordis 内核组合插件能力。", "The Cordis kernel composes plugin capabilities.")),
        (
            ("模型与工具", "Models and tools"),
            (
                "模型适配器负责路由；工具通过 defineTool 注册。",
                "Model adapters route requests; tools register through defineTool.",
            ),
        ),
        (
            ("会话与界面", "Sessions and UI"),
            ("工作区、会话与 Web 界面各自作为插件。", "Workspace, sessions and the web interface are plugins."),
        ),
        (
            ("powercontext-dsh", "powercontext-dsh"),
            ("提供项目记忆召回与工作交接能力。", "Provides project-memory recall and handoff capabilities."),
        ),
    ],
)
add(
    D,
    "two-process-architecture",
    ("两个进程，通过 HTTP 连接", "Two processes connected over HTTP"),
    [
        (
            ("浏览器", "Browser"),
            (
                "使用本地 Harness 界面，不直接调用 Server。",
                "Uses the local Harness UI; does not call the Server directly.",
            ),
        ),
        (
            ("powercontext-dsh", "powercontext-dsh"),
            (
                "Node 适配器调用 prepare_context、capture_source 和 pc_* 工具。",
                "The Node adapter calls prepare_context, capture_source and pc_* tools.",
            ),
        ),
        (
            ("PowerContext Server", "PowerContext Server"),
            (
                "独立 Python 进程通过 HTTP /v1 提供记忆与检索。",
                "An independent Python process serves memory and retrieval over HTTP /v1.",
            ),
        ),
    ],
)
add(
    D,
    "recall-timeline",
    ("每轮模型调用前的召回与采集", "Recall and capture before a model step"),
    [
        (("01 用户输入", "01 User message"), ("用户消息进入 Harness。", "The user message enters Harness.")),
        (
            ("02 prepare_context", "02 prepare_context"),
            ("从 Server 获取有预算边界的上下文。", "Fetch bounded context from the Server."),
        ),
        (
            ("03 注入证据", "03 Inject evidence"),
            (
                "将召回内容作为不可信历史，不作为指令。",
                "Treat recalled content as untrusted history, not instructions.",
            ),
        ),
        (
            ("04 capture_source", "04 capture_source"),
            ("将用户输入作为 Source 保存。", "Store the user input as Source."),
        ),
        (
            ("05 按需使用记忆", "05 Use memory when needed"),
            (
                "模型通过 pc_remember / pc_search 显式读写。",
                "The model explicitly reads or writes through pc_remember / pc_search.",
            ),
        ),
    ],
)


def units(s):
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def wrap(s, limit):
    lines, line = [], ""
    # Preserve English words and wrap Chinese at character boundaries.
    import re

    for token in re.findall(r"[A-Za-z0-9_./+%*-]+|.", s):
        if line and units(line + token) > limit:
            lines.append(line.strip())
            line = ""
        line += token
    if line.strip():
        lines.append(line.strip())
    return lines


def render(folder, name, titles, cards, footers):
    cols = 2 if len(cards) % 2 == 0 or len(cards) > 3 else 3
    width = (904 - (cols - 1) * 24) / cols
    limit = int((width - 48) / 11)
    paired = [[(wrap(t[i], limit), wrap(b[i], limit)) for t, b in cards] for i in range(2)]
    rows = (len(cards) + cols - 1) // cols
    heights = []
    for row in range(rows):
        heights.append(
            max(80 + len(t) * 30 + len(b) * 29 for edition in paired for t, b in edition[row * cols : (row + 1) * cols])
        )
    height = 174 + sum(heights) + (rows - 1) * 24 + 82
    for idx, lang in enumerate(("zh", "en")):
        parts = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="{height}" viewBox="0 0 1000 {height}" lang="{lang}" role="img" aria-labelledby="title"><title id="title">{escape(titles[idx])}</title><rect width="1000" height="{height}" rx="18" fill="#f7f5ef"/><style>text{{font-family:Arial,"PingFang SC","Microsoft YaHei",sans-serif;fill:#14213a}}</style>'
        ]

        def text(x, y, s, size=21, weight=400, color="#14213a", parts=parts):
            parts.append(
                f'<text x="{x}" y="{y}" font-size="{size}" font-weight="{weight}" style="fill:{color}">{escape(s)}</text>'
            )

        text(48, 43, "PowerContext", 18, 700, "#1769d2")
        for i, line in enumerate(wrap(titles[idx], 52)):
            text(48, 88 + i * 38, line, 30, 700)
        y = 156
        for row, h in enumerate(heights):
            for col, (ts, bs) in enumerate(paired[idx][row * cols : (row + 1) * cols]):
                x = 48 + col * (width + 24)
                parts.append(
                    f'<rect x="{x}" y="{y}" width="{width}" height="{h}" rx="14" fill="white" stroke="#cbd3dc"/>'
                )
                for j, line in enumerate(ts):
                    text(x + 24, y + 42 + j * 30, line, 22, 700, "#1769d2")
                for j, line in enumerate(bs):
                    text(x + 24, y + 64 + len(ts) * 30 + j * 29, line, 20)
            y += h + 24
        for i, line in enumerate(wrap(footers[idx], 100)):
            text(48, y + 12 + i * 25, line, 16, color="#5c6a7c")
        parts.append("</svg>")
        (ROOT / f"docs/assets/blog/{folder}/{name}.{lang}.svg").write_text("\n".join(parts) + "\n")


if __name__ == "__main__":
    for figure in FIGURES:
        render(*figure)
