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

"""Atomic preview wire compatibility over the shared endpoint signer."""

from powercontext.builtin.artifacts.atomic_memory.models import AtomicMemoryRead
from powercontext.builtin.artifacts.atomic_memory.service import ATOMIC_MERGE_ERRORS
from powercontext.builtin.artifacts.merge_restoration import ArtifactMergePreviewSigner


class AtomicMemoryPreviewSigner(ArtifactMergePreviewSigner):
    def __init__(self, *, keys, active_key_id, ttl_seconds=300, clock=None):
        super().__init__(
            keys=keys,
            active_key_id=active_key_id,
            ttl_seconds=ttl_seconds,
            clock=clock,
            endpoint_type=AtomicMemoryRead,
            preview_format="powercontext.atomic-memory.restoration-preview.v1",
            errors=ATOMIC_MERGE_ERRORS,
        )
