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

"""Stable identities shared by the frozen v1 import and legacy adapters."""

import json
from uuid import UUID, uuid5

_LEGACY_ENTRY_NAMESPACE = UUID("e6d8dc51-e317-50bc-b379-bbe3a577f443")


def legacy_entry_artifact_id(scope_id: str, memory_artifact_id: str, entry_id: str, /) -> str:
    """Preserve the complete legacy identity, including its owning container."""

    identity = json.dumps((scope_id, memory_artifact_id, entry_id), ensure_ascii=False, separators=(",", ":"))
    return "mem-" + uuid5(_LEGACY_ENTRY_NAMESPACE, identity).hex


__all__ = ["legacy_entry_artifact_id"]
