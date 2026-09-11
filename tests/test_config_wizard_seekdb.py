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

"""Background seekdb dependency installation without real network access."""

from __future__ import annotations

from threading import Event

import powercontext.cli.config_wizard_seekdb as seekdb_installer
from powercontext.cli.config_wizard_seekdb import (
    ALIYUN_SIMPLE_INDEX,
    CommandResult,
    SeekDBInstallPlan,
    declared_seekdb_requirements,
    inspect_seekdb_dependency,
    is_china_timezone,
    run_seekdb_install,
    start_seekdb_install,
)


def test_declared_requirements_use_current_release_extra_and_drop_self_reference() -> None:
    declared = [
        "powercontext[builtin]; extra == 'seekdb'",
        "pylibseekdb>=1.3,<2; (sys_platform == 'linux' or sys_platform == 'darwin') and extra == 'seekdb'",
        "windows-only>=1; sys_platform == 'win32' and extra == 'seekdb'",
        "unrelated>=1; extra == 'server'",
    ]

    requirements = declared_seekdb_requirements(declared, platform="linux")

    assert requirements == ("pylibseekdb<2,>=1.3",)


def test_normal_source_is_resolved_and_installed_before_validation() -> None:
    commands: list[tuple[str, ...]] = []

    def runner(command: tuple[str, ...]) -> CommandResult:
        commands.append(command)
        return CommandResult(0, "")

    plan = SeekDBInstallPlan("uv", "/tool/bin/python", ("pylibseekdb<2,>=1.3",))
    result = run_seekdb_install(plan, runner=runner, version_reader=lambda: "1.3.0")

    assert result.status == "ready"
    assert result.version == "1.3.0"
    assert "--dry-run" in commands[0]
    assert "--dry-run" not in commands[1]
    assert "--default-index" not in commands[0]


def test_failed_normal_source_falls_back_to_aliyun() -> None:
    commands: list[tuple[str, ...]] = []

    def runner(command: tuple[str, ...]) -> CommandResult:
        commands.append(command)
        if "--default-index" not in command:
            return CommandResult(2, "network timeout")
        return CommandResult(0, "")

    plan = SeekDBInstallPlan("uv", "/tool/bin/python", ("pylibseekdb<2,>=1.3",))
    result = run_seekdb_install(plan, runner=runner, version_reader=lambda: "1.3.0")

    assert result.status == "ready"
    assert any(ALIYUN_SIMPLE_INDEX in command for command in commands)


def test_china_timezone_uses_aliyun_without_trying_the_default_source() -> None:
    commands: list[tuple[str, ...]] = []

    def runner(command: tuple[str, ...]) -> CommandResult:
        commands.append(command)
        return CommandResult(0, "")

    plan = SeekDBInstallPlan("uv", "/tool/bin/python", ("pylibseekdb<2,>=1.3",), prefer_aliyun=True)
    result = run_seekdb_install(plan, runner=runner, version_reader=lambda: "1.3.0")

    assert result.status == "ready"
    assert commands
    assert all(ALIYUN_SIMPLE_INDEX in command for command in commands)


def test_china_timezone_names_are_detected_without_using_locale() -> None:
    assert is_china_timezone("Asia/Shanghai")
    assert is_china_timezone("PRC")
    assert not is_china_timezone("Asia/Singapore")
    assert not is_china_timezone("zh_CN.UTF-8")


def test_explicit_timezone_takes_precedence_over_machine_timezone(monkeypatch) -> None:
    monkeypatch.setenv("TZ", "UTC")
    assert not seekdb_installer._system_uses_china_timezone()
    monkeypatch.setenv("TZ", "Asia/Shanghai")
    assert seekdb_installer._system_uses_china_timezone()


def test_dependency_inspection_prefers_aliyun_for_china_timezone(monkeypatch) -> None:
    monkeypatch.delenv("UV_DEFAULT_INDEX", raising=False)
    monkeypatch.delenv("UV_INDEX_URL", raising=False)
    monkeypatch.setattr(seekdb_installer, "_validated_version", lambda: None)
    monkeypatch.setattr(seekdb_installer, "_system_uses_china_timezone", lambda: True)
    monkeypatch.setattr(seekdb_installer.shutil, "which", lambda _name: "/usr/bin/uv")
    monkeypatch.setattr(
        seekdb_installer.importlib.metadata,
        "requires",
        lambda _name: ("pylibseekdb>=1.3,<2; extra == 'seekdb'",),
    )

    dependency = inspect_seekdb_dependency()

    assert dependency.plan is not None
    assert dependency.plan.prefer_aliyun


def test_explicit_uv_index_overrides_china_timezone_default() -> None:
    commands: list[tuple[str, ...]] = []
    plan = SeekDBInstallPlan(
        "uv",
        "/tool/bin/python",
        ("pylibseekdb<2,>=1.3",),
        explicit_index=True,
        prefer_aliyun=True,
    )

    run_seekdb_install(
        plan,
        runner=lambda command: commands.append(command) or CommandResult(0, ""),
        version_reader=lambda: "1.3.0",
    )

    assert commands
    assert all("--default-index" not in command for command in commands)


def test_background_task_starts_without_waiting_for_installation() -> None:
    entered = Event()
    release = Event()

    def runner(_command: tuple[str, ...]) -> CommandResult:
        entered.set()
        release.wait(timeout=2)
        return CommandResult(0, "")

    plan = SeekDBInstallPlan("uv", "/tool/bin/python", ("pylibseekdb<2,>=1.3",))
    task = start_seekdb_install(plan, runner=runner, version_reader=lambda: "1.3.0")

    assert entered.wait(timeout=1)
    assert not task.done()
    release.set()
    assert task.wait(timeout=2).status == "ready"


def test_cancelled_background_task_does_not_start_a_fallback() -> None:
    entered = Event()
    release = Event()
    calls = 0

    def runner(_command: tuple[str, ...]) -> CommandResult:
        nonlocal calls
        calls += 1
        entered.set()
        release.wait(timeout=2)
        return CommandResult(2, "cancelled")

    plan = SeekDBInstallPlan("uv", "/tool/bin/python", ("pylibseekdb<2,>=1.3",))
    task = start_seekdb_install(plan, runner=runner, version_reader=lambda: None)
    assert entered.wait(timeout=1)

    task.cancel()
    release.set()

    assert task.wait(timeout=2).status == "cancelled"
    assert calls == 1


def test_failure_returns_one_sanitized_incremental_command() -> None:
    plan = SeekDBInstallPlan("uv", "/tool dir/bin/python", ("pylibseekdb<2,>=1.3",))

    result = run_seekdb_install(
        plan,
        runner=lambda _command: CommandResult(2, "https://user:secret@example.invalid failed"),
        version_reader=lambda: None,
    )

    assert result.status == "failed"
    assert "secret" not in result.reason
    assert result.manual_command.count("uv pip install") == 1
    assert "--default-index https://mirrors.aliyun.com/pypi/simple/" in result.manual_command
    assert "'/tool dir/bin/python'" in result.manual_command


def test_incompatible_platform_does_not_offer_a_doomed_manual_command() -> None:
    plan = SeekDBInstallPlan("uv", "/tool/bin/python", ("pylibseekdb<2,>=1.3",))

    result = run_seekdb_install(
        plan,
        runner=lambda _command: CommandResult(
            1,
            "No solution found when resolving dependencies: pylibseekdb has no wheels with a matching Python ABI tag",
        ),
        version_reader=lambda: None,
    )

    assert result.status == "unsupported"
    assert result.manual_command == ""
