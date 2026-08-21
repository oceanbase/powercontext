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

"""Official SWE-bench Pro prediction encoding."""

from __future__ import annotations

import json

from powercontext_eval.errors import PowerContextEvalError


class BinaryPatchError(PowerContextEvalError):
    """The MVP does not accept binary Git patches."""


def encode_predictions(instance_id: str, patch: str, prefix: str) -> str:
    """Encode exactly the official single-instance JSON array."""

    if not all(isinstance(value, str) and value for value in (instance_id, prefix)):
        raise ValueError("Prediction identity and prefix must be non-empty strings")
    if not isinstance(patch, str):
        raise TypeError("Patch must be text")
    return json.dumps(
        [{"instance_id": instance_id, "patch": patch, "prefix": prefix}],
        ensure_ascii=False,
        separators=(",", ":"),
    )
