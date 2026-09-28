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

"""Safe Profile HTML and deterministic, exact Handoff Markdown."""

import json
import re
from html import escape
from typing import Any
from urllib.parse import urlsplit

from markdown_it import MarkdownIt
from markdown_it.renderer import RendererHTML
from markdown_it.token import Token
from markdown_it.utils import EnvType, OptionsDict
from markupsafe import Markup
from typing_extensions import override

from powercontext.builtin.artifacts.handoff.models import HandoffContent
from powercontext.server.dashboard.api import ReadError
from powercontext.server.dashboard.preferences import CATALOGS

MAX_DOWNLOAD_BYTES = 10 * 1024 * 1024


def _image_text(self: RendererHTML, tokens: list[Token], index: int, options: OptionsDict, env: EnvType) -> str:
    return escape(tokens[index].content)


def _external_link(self: RendererHTML, tokens: list[Token], index: int, options: OptionsDict, env: EnvType) -> str:
    tokens[index].attrSet("rel", "noopener noreferrer")
    tokens[index].attrSet("target", "_blank")
    return RendererHTML().renderToken(tokens, index, options, env)


class _ProfileMarkdown(MarkdownIt):
    @override
    def validateLink(self, url: str) -> bool:
        try:
            return super().validateLink(url) and urlsplit(url).scheme.lower() in {"http", "https", "mailto"}
        except ValueError:
            return False


def profile_html(content: str) -> Markup:
    parser = _ProfileMarkdown("commonmark", {"html": False})
    parser.add_render_rule("image", _image_text)
    parser.add_render_rule("link_open", _external_link)
    # Only parser-produced HTML reaches the template; raw HTML and resource images are disabled.
    return Markup(parser.render(content))  # noqa: S704


def _literal(value: str) -> str:
    # Any indented line can form a code block, where Markdown escapes become
    # literal. Fence the complete value to preserve text and indentation together.
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    if re.search(r"(?m)^(?: {4}| {0,3}\t)", value):
        fence = "`" * max(3, 1 + max((len(run) for run in re.findall(r"`+", value)), default=0))
        controls = "".join(
            char if char in {"\n", "\t"} or (ord(char) >= 32 and ord(char) != 127) else f"&#{ord(char)};"
            for char in value
        )
        return f"{fence}\n{controls}\n{fence}"
    special = set(r"\\`*_{}[]()#+-.!|>~:=")
    result: list[str] = []
    for char in value:
        if char == "\n":
            result.append(char)
        elif char == "&":
            result.append("&amp;")
        elif char == "<":
            result.append("&lt;")
        elif char == ">":
            result.append("&gt;")
        elif char in special:
            result.extend(("\\", char))
        elif ord(char) < 32 or ord(char) == 127:
            result.append(f"&#{ord(char)};")
        else:
            result.append(char)
    return "".join(result)


def _json_block(value: Any) -> str:
    body = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
    fence = "`" * max(3, 1 + max((len(run) for run in re.findall(r"`+", body)), default=0))
    return f"{fence}json\n{body}\n{fence}"


def handoff_markdown(record: dict[str, Any], language: str) -> bytes:
    content = HandoffContent.model_validate_json(json.dumps(record["content"]))
    labels = CATALOGS[language]
    identity = {key: record[key] for key in ("scope_id", "family", "artifact_id", "revision", "content_digest")}
    lines = [
        f"# {labels['handoff']}",
        "",
        "`powercontext.handoff-markdown.v1`",
        "",
        _json_block(identity),
        "",
        f"## {labels['handoff_objective']}",
        "",
        _literal(content.objective),
        "",
        f"{labels['handoff_status']}: {content.disposition}",
        "",
        f"## {labels['recorded_state']}",
    ]
    for index, statement in enumerate(content.state, 1):
        lines.extend([
            "",
            f"### {index}",
            "",
            _literal(statement.text),
            "",
            _json_block([citation.model_dump(mode="json", by_alias=True) for citation in statement.citations]),
        ])
    lines.extend(["", f"## {labels['next_action']}", ""])
    if content.next_action is None:
        lines.append(labels["no_next_action"])
    else:
        lines.extend([
            _literal(content.next_action.text),
            "",
            _json_block([
                citation.model_dump(mode="json", by_alias=True) for citation in content.next_action.citations
            ]),
        ])
    lines.extend(["", f"## {labels['handoff_omissions']}", ""])
    if not content.omissions:
        lines.append(labels["no_omissions"])
    for index, omission in enumerate(content.omissions, 1):
        lines.extend([f"### {index}", "", _literal(omission.text), ""])
        if omission.citation is not None:
            lines.extend([_json_block(omission.citation.model_dump(mode="json", by_alias=True)), ""])
    lines.extend([
        "",
        f"## {labels['profile_references']}",
        "",
        _json_block({"sources": record["sources"], "artifacts": record["artifacts"]}),
        "",
    ])
    result = "\n".join(lines).encode("utf-8")
    if len(result) > MAX_DOWNLOAD_BYTES:
        raise ReadError(413, "download_too_large")
    return result
