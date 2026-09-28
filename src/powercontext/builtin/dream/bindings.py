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

"""Map explicit Dream operations to their output Family's processing binding."""

from powercontext.builtin.artifacts.experience import EXPERIENCE_INCUBATION_CURSOR_NAME

SKILL_DREAM_BINDING = "skill.dream.v1"
DREAM_BINDINGS = {
    "refine_experience": EXPERIENCE_INCUBATION_CURSOR_NAME,
    "derive_skill": SKILL_DREAM_BINDING,
}
DREAM_PROVIDERS = frozenset({"openai", "openai-chat", "openai-responses", "anthropic"})
