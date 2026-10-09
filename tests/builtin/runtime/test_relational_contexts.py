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

from __future__ import annotations

from powercontext.builtin.runtime.relational import RelationalContexts


def test_evict_removes_scope_skill_publication_locks() -> None:
    contexts = RelationalContexts.__new__(RelationalContexts)
    contexts._contexts = {}
    contexts._source_locks = {}
    contexts._activation_locks = {}
    contexts._experience_locks = {}
    contexts._skill_publication_locks = {
        ("scope-a", "target-1", "skill-1"): object(),
        ("scope-b", "target-1", "skill-1"): object(),
    }

    contexts.evict("scope-a")

    assert ("scope-a", "target-1", "skill-1") not in contexts._skill_publication_locks
    assert ("scope-b", "target-1", "skill-1") in contexts._skill_publication_locks
