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

"""What the shared lifecycle modules need to know about Codex."""

from powercontext_lifecycle.bootstrap_state import BootstrapState
from powercontext_lifecycle.diagnostics import DiagnosticThrottle

BOOTSTRAP_STATE = BootstrapState(
    directory_variable="POWERCONTEXT_BOOTSTRAP_STATE_DIR",
    plugin_data_variable="PLUGIN_DATA",
)
DIAGNOSTICS = DiagnosticThrottle(state_file="codex-diagnostics.json")
