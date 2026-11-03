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

"""Opt-in container acceptance using an already cached SWE-bench Pro task image.

Run with ``POWERCONTEXT_EVAL_TEST_TASK_IMAGE=<cached-image> uv run --project evaluation pytest
-c evaluation/pyproject.toml evaluation/coding/swebench_pro/tests/contract/test_workspace_container.py -q``.
"""

from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import pytest

from powercontext_eval_swebench_pro.models import Arm
from powercontext_eval_swebench_pro.powercontext_sut import ArmPaths, DockerSut, SutConfig, trim_future_history
from powercontext_eval_swebench_pro.process import CommandResult, ProcessRunner


@pytest.mark.live
def test_agent_container_hides_image_history_and_shares_workspace_changes(tmp_path: Path) -> None:
    image = os.environ.get("POWERCONTEXT_EVAL_TEST_TASK_IMAGE")
    if not image:
        pytest.skip("Set POWERCONTEXT_EVAL_TEST_TASK_IMAGE to a cached task image containing future history")

    runner = ProcessRunner()

    def run(*argv: str, check: bool = True) -> CommandResult:
        return runner.run(argv, cwd=tmp_path, timeout=60, check=check)

    inspection = run("docker", "image", "inspect", "--format={{.Id}}", image, check=False)
    assert inspection.returncode == 0, "The configured task image must already be cached; this test never pulls"
    image_id = inspection.stdout.strip()
    container = f"powercontext-history-test-{uuid4().hex[:12]}"
    original_container = f"{container}-original"
    try:
        future = run(
            "docker",
            "run",
            "--rm",
            "--pull=never",
            "--name",
            original_container,
            "--network",
            "none",
            "--cpus",
            "1",
            "--memory",
            "512m",
            "--pids-limit",
            "64",
            "--entrypoint",
            "git",
            image_id,
            "-C",
            "/app",
            "rev-list",
            "--max-count=1",
            "--all",
            "--not",
            "HEAD",
        ).stdout.strip()
        assert future, "The selected image must contain a commit outside HEAD's ancestors"

        workspace = tmp_path / "workspace"
        runtime = tmp_path / "runtime"
        source = tmp_path / "source"
        root_home = runtime / "root-home"
        tools = tmp_path / "tools"
        for directory in (workspace, source, root_home, tools):
            directory.mkdir(parents=True)
        for tool in ("codex", "uv"):
            (tools / tool).write_text("unused test tool\n")

        run("git", "-C", str(workspace), "init", "--quiet", "--initial-branch=main")
        module = workspace / "module.py"
        module.write_text("value = 1\n")
        run("git", "-C", str(workspace), "add", "module.py")
        run(
            "git",
            "-C",
            str(workspace),
            "-c",
            "user.name=fixture",
            "-c",
            "user.email=fixture@example.test",
            "commit",
            "--quiet",
            "-m",
            "base",
        )
        base = run("git", "-C", str(workspace), "rev-parse", "HEAD").stdout.strip()
        trim_future_history(runner, workspace)

        paths = ArmPaths(
            source=source,
            auth_source=tmp_path / "unused-auth.json",
            workspace=workspace,
            runtime=runtime,
            codex_home=root_home / ".codex",
            pc_home=runtime / "pc-home",
            tokensflow_home=root_home,
            result_root=tmp_path / "results",
        )
        config = SutConfig(
            run_id=container,
            task_image=image_id,
            codex_binary=tools / "codex",
            uv_binary=tools / "uv",
            source_checkout=source,
            plugin_checkout_sha=base,
        )
        # Keep the production launch command and mounts; replace only the staged server executable.
        server = runtime / "pc-env/bin/powercontext"
        server.parent.mkdir(parents=True)
        server.write_text("#!/bin/sh\nexec sleep 120\n")
        server.chmod(0o755)
        sut = DockerSut(runner)
        sut._stage_recorder(config, paths)
        sut._start_container(config, Arm.OFF, paths, "none", container, None, {}, ())

        for checkout in ("/workspace", "/app"):
            # The host-created fixture has a different UID from the container's root user.
            git = ("docker", "exec", container, "git", "-c", f"safe.directory={checkout}", "-C", checkout)
            head = run(*git, "rev-parse", "HEAD", check=False)
            assert head.returncode == 0, head.stderr
            assert head.stdout.strip() == base
            assert run(*git, "cat-file", "-e", future, check=False).returncode != 0
        run("docker", "exec", container, "/bin/sh", "-c", "printf 'value = 2\\n' > /app/module.py")
        assert run("docker", "exec", container, "cat", "/workspace/module.py").stdout == "value = 2\n"
        assert "+value = 2" in run("git", "-C", str(workspace), "diff", "HEAD").stdout
    finally:
        run("docker", "rm", "-f", container, original_container, check=False)
