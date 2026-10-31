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

import shutil

import pytest

from powercontext_eval_capture_ownership import replay
from powercontext_eval_capture_ownership.host import repository_root
from powercontext_eval_capture_ownership.models import RunConfig


@pytest.fixture
def frozen_checkout(tmp_path, monkeypatch):
    source = repository_root()
    paths = [
        "uv.lock",
        "evaluation/uv.lock",
        "pyproject.toml",
        "evaluation/pyproject.toml",
        "src/powercontext/builtin/sources/content.py",
        "src/powercontext/builtin/artifacts/atomic_memory/extraction.py",
        "src/powercontext/builtin/artifacts/prompt/builtin.py",
        "src/powercontext/builtin/inference/pydantic_ai.py",
        "src/powercontext/builtin/runtime/composition.py",
        "integrations/workbuddy/plugins/powercontext/hooks/workbuddy_powercontext_hook.py",
        "integrations/workbuddy/plugins/powercontext/hooks/prepared_context.py",
        "evaluation/memory/capture_ownership/src/powercontext_eval_capture_ownership/replay.py",
    ]
    for name in paths:
        destination = tmp_path / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / name, destination)
    # Keep the commit fixed while changing working-file contents in an isolated copy.
    monkeypatch.setattr(replay.subprocess, "check_output", lambda *_args, **_kwargs: "a" * 40)
    return tmp_path


@pytest.mark.parametrize(
    "name",
    [
        "src/powercontext/builtin/inference/pydantic_ai.py",
        "src/powercontext/builtin/runtime/composition.py",
        "integrations/workbuddy/plugins/powercontext/hooks/prepared_context.py",
    ],
)
def test_runtime_edits_invalidate_the_lock_with_the_same_commit(manifest, frozen_checkout, name):
    config = RunConfig(schema_version="capture-ownership.config.v1")
    lock = replay.freeze(frozen_checkout, manifest, config)
    replay.verify_lock(frozen_checkout, manifest, lock)
    path = frozen_checkout / name
    path.write_bytes(path.read_bytes() + b"\n# Changed inference or host behavior.\n")
    with pytest.raises(ValueError, match="differs from the frozen experiment"):
        replay.verify_lock(frozen_checkout, manifest, lock)


@pytest.mark.parametrize("change", ["add", "delete"])
def test_runtime_file_inventory_changes_invalidate_the_lock(manifest, frozen_checkout, change):
    config = RunConfig(schema_version="capture-ownership.config.v1")
    lock = replay.freeze(frozen_checkout, manifest, config)
    if change == "add":
        (frozen_checkout / "src/powercontext/builtin/inference/new_policy.py").write_text("ENABLED = True\n")
    else:
        (frozen_checkout / "src/powercontext/builtin/inference/pydantic_ai.py").unlink()
    with pytest.raises(ValueError, match="differs from the frozen experiment"):
        replay.verify_lock(frozen_checkout, manifest, lock)
