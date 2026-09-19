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

"""Deployment-owned repository access and resource policies."""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from powercontext.paths import powercontext_data_dir


class CodeGraphConfig(BaseModel):
    """Locate an explicitly installed CodeGraph standalone release."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: Literal["codegraph"] = "codegraph"
    executable: str = "codegraph"


class CodeLimits(BaseModel):
    """Bound input capture, cache storage, engine execution, and retries."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_files: int = Field(default=20_000, ge=1)
    max_file_bytes: int = Field(default=2 * 1024 * 1024, ge=1)
    max_content_bytes: int = Field(default=512 * 1024 * 1024, ge=1)
    max_cache_bytes: int = Field(default=2 * 1024 * 1024 * 1024, ge=1)
    build_timeout_seconds: float = Field(default=600, gt=0)
    query_timeout_seconds: float = Field(default=5, gt=0)
    capture_attempts: int = Field(default=2, ge=1, le=5)


class CodeConfig(BaseModel):
    """Map existing Scopes to local repositories without a new stored resource."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    repositories: dict[str, Path] = Field(default_factory=dict)
    provider: CodeGraphConfig = Field(default_factory=CodeGraphConfig)
    cache_dir: Path = Field(default_factory=lambda: powercontext_data_dir() / "code")
    include_untracked: bool = False
    exclude: tuple[str, ...] = ()
    limits: CodeLimits = Field(default_factory=CodeLimits)
