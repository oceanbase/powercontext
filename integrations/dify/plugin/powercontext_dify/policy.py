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

"""Model-facing parameter policy shared by declarations and SDK validation."""

HIDDEN_PARAMETERS = frozenset({"scope_id", "tag_filter", "expected_revision", "max_bytes", "include_code", "assembly"})
MEMORY_KINDS = ("decision", "constraint", "current-state", "task-outcome", "next-step", "agent-note")
