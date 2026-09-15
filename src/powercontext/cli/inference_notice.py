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

import typer


def write_inference_capability_notice(
    *,
    generation_model: str | None,
    embedding_model: str | None,
) -> None:
    """Print an actionable notice for every missing inference capability."""

    if generation_model is not None and embedding_model is not None:
        return

    typer.secho("\nInference capability notice", bold=True, fg=typer.colors.YELLOW)
    typer.echo("PowerContext Server generation or embedding models are not fully configured.")
    typer.echo("Some artifact features may be unavailable. See the configuration guide:")
    typer.echo("https://powercontext.oceanbase.io/en/docs/reference/configuration/")


__all__ = ["write_inference_capability_notice"]
