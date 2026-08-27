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

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest


def _load_script(name: str) -> ModuleType:
    path = Path(__file__).parents[1] / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


smoke = _load_script("ci_release_smoke")


@pytest.mark.skipif(smoke.os.name == "nt", reason="POSIX venv symlink regression")
def test_release_smoke_resolves_console_script_from_verification_python(tmp_path) -> None:
    scripts = tmp_path / "verification" / "bin"
    scripts.mkdir(parents=True)
    base_python = tmp_path / "base-python"
    base_python.touch()
    python = scripts / "python"
    python.symlink_to(base_python)
    console_script = scripts / "powercontext"
    console_script.touch()

    assert smoke._console_script(python) == console_script
