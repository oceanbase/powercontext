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

"""Explain capability degradation when a deployment omits inference models."""

from __future__ import annotations

import shlex
from pathlib import Path

import typer


def write_inference_capability_notice(
    *,
    generation_model: str | None,
    embedding_model: str | None,
    env_file: Path | None = None,
) -> None:
    """Print an actionable notice for every missing inference capability."""

    generation_missing = generation_model is None
    embedding_missing = embedding_model is None
    if not generation_missing and not embedding_missing:
        return

    typer.secho("\nInference capability notice", bold=True, fg=typer.colors.YELLOW)
    if generation_missing and embedding_missing:
        typer.echo("本次部署没有配置 generation 或 embedding model, Server 仍可启动并提供基础 Memory/Source 能力。")
    elif generation_missing:
        typer.echo("本次部署没有配置 generation model, Server 仍可启动, 但生成类能力不可用。")
    else:
        typer.echo("本次部署没有配置 embedding model, Server 仍可启动, 但语义检索能力会退化。")

    if generation_missing:
        typer.echo("未配置 generation model:")
        typer.echo("  - Source 自动抽取 Memory、Memory rerank、Experience/Skill/Handoff 生成和 Prompt demonstration 不可用。")
    if embedding_missing:
        typer.echo("未配置 embedding model:")
        typer.echo("  - 向量检索和 hybrid 检索不可用; Memory、Experience 等检索会退化为 FTS (关键词检索)。")

    if env_file is None:
        typer.echo("如需启用上述能力, 请配置 Server inference model、provider 凭据和所需的 embedding profile 信息。")
        return
    quoted = shlex.quote(str(env_file.expanduser().resolve()))
    typer.echo("如需启用上述能力, 请编辑环境文件, 配置 Server inference model、provider 凭据和所需的 embedding profile 信息,")
    typer.echo(f"然后运行: powercontext config validate --env-file {quoted}")


__all__ = ["write_inference_capability_notice"]
