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

"""Native current-user service adapters."""

from __future__ import annotations

import sys

from powercontext.service.adapters.base import NativeServiceAdapter, UnsupportedAdapter


def native_service_adapter() -> NativeServiceAdapter:
    if sys.platform == "linux":
        from powercontext.service.adapters.systemd import SystemdUserAdapter

        return SystemdUserAdapter()
    if sys.platform == "darwin":
        from powercontext.service.adapters.launchd import LaunchdUserAdapter

        return LaunchdUserAdapter()
    if sys.platform == "win32":
        from powercontext.service.adapters.windows import WindowsTaskSchedulerAdapter

        return WindowsTaskSchedulerAdapter()
    return UnsupportedAdapter(f"personal service installation is not supported on {sys.platform}")


__all__ = ["NativeServiceAdapter", "native_service_adapter"]
