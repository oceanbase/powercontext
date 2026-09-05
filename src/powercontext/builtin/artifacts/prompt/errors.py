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

"""Body-free errors for operational Prompt management and inference."""


class PromptError(ValueError):
    """Expose a stable public code without echoing instructions or model output."""

    def __init__(self, code: str, *, during_inference: bool = False) -> None:
        self.code = code
        self.during_inference = during_inference
        messages = {
            "unknown_prompt_key": "Prompt key is not registered",
            "prompt_customization_unavailable": "The effective component cannot honor a custom Prompt",
            "prompt_definition_incompatible": "Prompt demonstrations do not match the deployed Definition",
            "invalid_prompt_content": "Prompt content does not satisfy the registered content contract",
            "invalid_prompt_demonstrations": "The provider did not return the required valid demonstrations",
            "invalid_handoff_generation": "Handoff generation provenance could not be verified",
        }
        super().__init__(messages[code])
