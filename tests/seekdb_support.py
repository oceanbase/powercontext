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

"""Linux process-exit ownership for native SeekDB acceptance tests."""

from __future__ import annotations

import ctypes
import os
import select
from typing import Any


class RestartingInstance:
    """Release a client and require its native process to exit before returning."""

    def __init__(self, instance: Any, native_pid: int, *, timeout: float = 10) -> None:
        self.instance = instance
        self.timeout = timeout
        self.descriptor: int | None = None
        try:
            if native_pid <= 0:
                raise ValueError("native process ID must be positive")  # noqa: TRY003, TRY301
            pidfd_open = ctypes.CDLL(None, use_errno=True).pidfd_open
            pidfd_open.argtypes = (ctypes.c_int, ctypes.c_uint)
            pidfd_open.restype = ctypes.c_int
            descriptor = pidfd_open(native_pid, 0)
            if descriptor < 0:
                raise OSError(ctypes.get_errno(), "could not open native pidfd")  # noqa: TRY301
            self.descriptor = descriptor
        except BaseException:
            instance.close()
            raise

    def connection_options(self) -> Any:
        return self.instance.connection_options()

    def close(self) -> None:
        descriptor = self.descriptor
        if descriptor is None:
            return
        self.descriptor = None
        try:
            self.instance.close()
            if not select.select([descriptor], [], [], self.timeout)[0]:
                message = "native process did not exit after last-client close"
                raise TimeoutError(message)
        finally:
            os.close(descriptor)
