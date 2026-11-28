// Copyright (c) 2026 OceanBase.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

pub(crate) const CODEX_TASK_OUTCOME_GUIDANCE: &str =
    "PowerContext task outcome guidance: Only when the task is actually complete or explicitly interrupted, call the PowerContext record_task_outcome tool if available. Skip it on intermediate turns. A prompt boundary, Stop, or session end alone is insufficient. Report the actual status and omit secrets.";
