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
import platform
import selectors
import sys
from typing import Any


def _open_pidfd(pid: int) -> int:
    """Open a Linux pidfd without requiring a recent host libc or Python build."""
    if pid <= 0:
        message = "native process ID must be positive"
        raise ValueError(message)
    if sys.platform != "linux":
        message = "native restart acceptance requires Linux pidfd support"
        raise NotImplementedError(message)
    python_open = getattr(os, "pidfd_open", None)
    if python_open is not None:
        return python_open(pid, 0)

    libc = ctypes.CDLL(None, use_errno=True)
    libc_open = getattr(libc, "pidfd_open", None)
    if libc_open is not None:
        libc_open.argtypes = (ctypes.c_int, ctypes.c_uint)
        libc_open.restype = ctypes.c_int
        descriptor = libc_open(pid, 0)
    else:
        # Linux UAPI: arch/x86/entry/syscalls/syscall_64.tbl and
        # include/uapi/asm-generic/unistd.h (used by aarch64).
        numbers = {"x86_64": 434, "aarch64": 434}
        number = numbers.get(platform.machine())
        if number is None or ctypes.sizeof(ctypes.c_void_p) != 8:
            message = "pidfd syscall fallback supports only the locked 64-bit x86_64 and aarch64 Linux wheels"
            raise NotImplementedError(message)
        syscall = libc.syscall
        syscall.argtypes = (ctypes.c_long, ctypes.c_int, ctypes.c_uint)
        syscall.restype = ctypes.c_long
        descriptor = syscall(number, pid, 0)
    if descriptor < 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    return descriptor


class RestartingInstance:
    """Release a client and require its native process to exit before returning."""

    def __init__(self, instance: Any, native_pid: int, *, timeout: float = 10) -> None:
        self.instance = instance
        self.timeout = timeout
        self.descriptor: int | None = None
        try:
            self.descriptor = _open_pidfd(native_pid)
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
            with selectors.DefaultSelector() as selector:
                selector.register(descriptor, selectors.EVENT_READ)
                if not selector.select(self.timeout):
                    message = "native process did not exit after last-client close"
                    raise TimeoutError(message)
        finally:
            os.close(descriptor)
