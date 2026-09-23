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

"""Shared command failures without host-specific exception types."""

from __future__ import annotations

from pathlib import Path


class SetupError(RuntimeError):
    """An actionable setup failure, handled consistently by every host command."""

    @classmethod
    def unavailable(cls, component: str) -> SetupError:
        return cls(f"{component} is not installed or is not on PATH.")

    @classmethod
    def not_found(cls, component: str, path: Path) -> SetupError:
        return cls(f"{component} was not found under {path}.")

    @classmethod
    def invalid_ref(cls, host: str, ref: str) -> SetupError:
        return cls(f"invalid {host} ref: {ref}")

    @classmethod
    def invalid_source(cls, host: str) -> SetupError:
        return cls(f"invalid {host} source; use a local path or an HTTPS/SSH GitHub repository")

    @classmethod
    def command_unavailable(cls, command: list[str], error: BaseException) -> SetupError:
        return cls(f"Cannot run {' '.join(command)}: {error}")

    @classmethod
    def command_failed(cls, command: list[str], detail: str) -> SetupError:
        return cls(f"`{' '.join(command)}` failed: {detail}")

    @classmethod
    def invalid_command_output(cls, command: list[str], detail: str) -> SetupError:
        return cls(f"`{' '.join(command)}` returned {detail}")
