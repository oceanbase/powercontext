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

"""Register PowerContext and execute the pinned LongMemEval-V2 harness unchanged."""

from __future__ import annotations

import argparse
import importlib
import runpy
import sys
from collections.abc import Sequence
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from typing import Any

from powercontext_eval.benchmarks.longmemeval_v2.adapter import PowerContextMemory
from powercontext_eval.benchmarks.longmemeval_v2.catalog import validate_harness_checkout


class LongMemEvalV2HarnessBootstrapError(RuntimeError):
    """The pinned harness could not be safely registered or executed."""


def run_pinned_harness(harness_root: Path, arguments: Sequence[str]) -> None:
    """Validate, register the adapter, and run the upstream harness in-process."""

    root = harness_root.resolve()
    validate_harness_checkout(root)
    harness_path = root / "evaluation" / "harness.py"
    memory_path = root / "memory_modules" / "memory.py"
    with _harness_imports(root):
        memory_module = importlib.import_module("memory_modules.memory")
        loaded_path = Path(_module_path(memory_module)).resolve()
        if loaded_path != memory_path:
            raise LongMemEvalV2HarnessBootstrapError(
                f"loaded LongMemEval-V2 memory contract from {loaded_path}, expected {memory_path}"
            )
        registry = getattr(memory_module, "MEMORY_TYPES", None)
        register_memory = getattr(memory_module, "register_memory", None)
        if not isinstance(registry, dict) or not callable(register_memory):
            raise LongMemEvalV2HarnessBootstrapError("LongMemEval-V2 memory registry contract is unavailable")
        existing = registry.get(PowerContextMemory.memory_type)
        if existing not in {None, PowerContextMemory}:
            raise LongMemEvalV2HarnessBootstrapError(
                "LongMemEval-V2 already registered a different powercontext backend"
            )
        register_memory(PowerContextMemory)
        with _arguments(harness_path, arguments):
            runpy.run_path(str(harness_path), run_name="__main__")


def main(argv: Sequence[str] | None = None) -> None:
    """CLI entry point intended to be run with the pinned harness Python."""

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--harness-root", type=Path, required=True)
    known, forwarded = parser.parse_known_args(argv)
    if forwarded[:1] == ["--"]:
        forwarded = forwarded[1:]
    run_pinned_harness(known.harness_root, forwarded)


def _module_path(module: ModuleType) -> str:
    value = getattr(module, "__file__", None)
    if not isinstance(value, str):
        raise LongMemEvalV2HarnessBootstrapError("LongMemEval-V2 memory contract has no file identity")
    return value


@contextmanager
def _arguments(harness_path: Path, arguments: Sequence[str]) -> Any:
    previous = sys.argv
    sys.argv = [str(harness_path), *arguments]
    try:
        yield
    finally:
        sys.argv = previous


@contextmanager
def _harness_imports(harness_root: Path) -> Any:
    root = str(harness_root)
    previous_path = list(sys.path)
    previous_modules = {
        name: module
        for name, module in sys.modules.items()
        if name == "memory_modules" or name.startswith("memory_modules.")
    }
    for name in previous_modules:
        del sys.modules[name]
    sys.path.insert(0, root)
    try:
        yield
    finally:
        sys.path[:] = previous_path
        for name in tuple(sys.modules):
            if name == "memory_modules" or name.startswith("memory_modules."):
                del sys.modules[name]
        sys.modules.update(previous_modules)


if __name__ == "__main__":
    main()
