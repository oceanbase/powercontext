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

"""Built-in pure Trigger policies."""

from powercontext.builtin.triggers.handoff import (
    HANDOFF_BOUNDARY_TRIGGER_NAME,
    HandoffBoundary,
    HandoffTrigger,
)
from powercontext.builtin.triggers.source_window import (
    SOURCE_WINDOW_TRIGGER_NAME,
    ProcessSourceWindow,
    SourceHighWatermark,
    SourceWindowTrigger,
)

__all__ = [
    "HANDOFF_BOUNDARY_TRIGGER_NAME",
    "SOURCE_WINDOW_TRIGGER_NAME",
    "HandoffBoundary",
    "HandoffTrigger",
    "ProcessSourceWindow",
    "SourceHighWatermark",
    "SourceWindowTrigger",
]
