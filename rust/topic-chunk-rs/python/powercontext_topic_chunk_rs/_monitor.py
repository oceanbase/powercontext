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

"""Sticky reference-call and native-identity checks for isolated acceptance processes."""

from __future__ import annotations

import ast
import sys
import threading
from pathlib import Path
from types import FrameType, ModuleType
from typing import Any

from . import _native
from ._adapter import chunk_topic_memory_detail


class PathViolation(RuntimeError):
    """The mandatory native execution path could not be established."""


class NativeMonitor:
    def __init__(self) -> None:
        self.violations: list[str] = []
        self.n = self.r = self.f = self.e = 0
        self._codes: set[Any] = set()
        self._hook = self._profile
        self._ranges = _native.chunk_ranges
        self._stats = _native.execution_stats
        self._lock = threading.RLock()
        self._old_profile = sys.getprofile()
        self._old_thread_profile = threading.getprofile()
        self._installed = False

    def _profile(self, frame: FrameType, event: str, arg: Any) -> None:
        if event == "call" and frame.f_code in self._codes:
            self.violations.append("reference_algorithm_entered")
            raise PathViolation(self.violations[-1])

    def check_health(self) -> None:
        if sys.getprofile() is not self._hook or threading.getprofile() is not self._hook:
            self.violations.append("profile_hook_lost")
        if _native.chunk_ranges is not self._ranges or _native.execution_stats is not self._stats:
            self.violations.append("native_binding_replaced")
        if self.violations:
            raise PathViolation(",".join(self.violations))

    def route(self, detail: str, /):
        if type(detail) is not str:
            return chunk_topic_memory_detail(detail)
        with self._lock:
            self.n += 1
            before = self._stats()
            violations = len(self.violations)
            try:
                self.check_health()
                return chunk_topic_memory_detail(detail)
            finally:
                if len(self.violations) > violations:
                    self.f += 1
                elif self._stats() - before == 1:
                    self.r += 1
                else:
                    self.e += 1

    @staticmethod
    def _check_adapter_imports() -> None:
        adapter = Path(__file__).with_name("_adapter.py")
        tree = ast.parse(adapter.read_text())
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module
                and node.module.startswith("powercontext")
                and node.module != "powercontext.builtin.artifacts.topic_memory.models"
            ):
                message = "Adapter imports reference algorithm"
                raise PathViolation(message)
            if isinstance(node, ast.Import) and any(alias.name.endswith(".chunking") for alias in node.names):
                message = "Adapter imports reference algorithm"
                raise PathViolation(message)

    def install(self) -> None:
        from powercontext.builtin.artifacts.topic_memory import chunking

        if self._installed or self._old_profile is not None or self._old_thread_profile is not None:
            message = "Native monitor requires an isolated unprofiled process"
            raise PathViolation(message)

        def selected(detail: str, /):
            return self.route(detail)

        selected.__name__ = selected.__qualname__ = "chunk_topic_memory_detail"
        original = chunking.chunk_topic_memory_detail
        names = (
            "chunk_topic_memory_detail",
            "_semantic_spans",
            "_split_oversized_block",
            "_trimmed_span",
            "_bounded_window_spans",
        )
        self._codes = {getattr(chunking, name).__code__ for name in names}
        self._check_adapter_imports()
        for module in tuple(sys.modules.values()):
            if not isinstance(module, ModuleType):
                continue
            for name, value in tuple(vars(module).items()):
                if value is original:
                    setattr(module, name, selected)
        for module in tuple(sys.modules.values()):
            if isinstance(module, ModuleType) and any(value is original for value in vars(module).values()):
                message = "An original public function alias remains loaded"
                raise PathViolation(message)
        sys.setprofile(self._hook)
        threading.setprofile(self._hook)
        self._installed = True

    def report(self) -> dict[str, Any]:
        return {
            "N": self.n,
            "R": self.r,
            "F": self.f,
            "E": self.e,
            "violations": self.violations,
            "native_completions": self._stats(),
            "native_path": _native.__file__,
        }

    def finish(self, *, require_calls: bool = True) -> dict[str, Any]:
        try:
            self.check_health()
            if self.n != self.r + self.f + self.e or self.f or self.e or (require_calls and not self.r):
                message = "Native path accounting did not satisfy N=R+F+E, R>0, F=E=0"
                raise PathViolation(message)
            return self.report()
        finally:
            sys.setprofile(self._old_profile)
            threading.setprofile(self._old_thread_profile)
