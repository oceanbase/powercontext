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

"""Failure-isolated helpers for operational logging."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from contextlib import suppress
from typing import Any


def log_safely(
    logger: logging.Logger,
    level: int,
    message: str,
    *,
    exc_info: BaseException | None = None,
    extra: Mapping[str, Any] | None = None,
) -> None:
    """Emit one operational record without changing authoritative behavior."""

    with suppress(Exception):
        logger.log(level, message, exc_info=exc_info, extra=None if extra is None else dict(extra))


__all__ = ["log_safely"]
