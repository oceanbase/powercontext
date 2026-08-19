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

"""Failures owned by the built-in Runtime application boundary."""

from powercontext.errors import PowerContextError


class InvalidRuntimeRequestError(PowerContextError, ValueError):
    """Raised when a scoped Runtime request is not valid for the current state."""

    def __init__(self, code: str) -> None:
        self.code = code
        messages = {
            "since-revision": "since_revision is newer than the current Memory Revision",
        }
        super().__init__(messages.get(code, f"invalid Runtime request: {code}"))


class PreparedContextInvariantError(PowerContextError, RuntimeError):
    """Raised when an internal source violates prepared-context construction."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(f"Prepared Context invariant failed: {code}")


__all__ = ["InvalidRuntimeRequestError"]
