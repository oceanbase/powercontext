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

"""Failures specific to Handoff Report projection."""

from powercontext.errors import PowerContextError


class HandoffReportError(PowerContextError):
    pass


class HandoffReportInconsistentError(HandoffReportError):
    def __init__(self, scope_id: str) -> None:
        self.scope_id = scope_id
        super().__init__(f"the exact Handoff could not be read for Scope {scope_id!r}")


class HandoffReportTooLargeError(HandoffReportError):
    def __init__(self, *, selected_scopes: int, estimated_bytes: int | None = None) -> None:
        self.selected_scopes = selected_scopes
        self.estimated_bytes = estimated_bytes
        super().__init__("the Handoff Report exceeds the response limit")


__all__ = ["HandoffReportError", "HandoffReportInconsistentError", "HandoffReportTooLargeError"]
