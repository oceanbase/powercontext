from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Sequence
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import cast

import pytest

from powercontext_eval import process as process_module
from powercontext_eval.process import (
    CommandFailed,
    CommandNotFound,
    CommandResult,
    CommandTimedOut,
    ProcessRunner,
)


def test_linux_process_scan_stops_accessing_entries_after_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    accessed: list[str] = []

    class FakeStat:
        def __init__(self, entry_name: str) -> None:
            self._entry_name = entry_name

        def read_text(self, *, encoding: str, errors: str) -> str:
            assert encoding == "utf-8"
            assert errors == "replace"
            accessed.append(self._entry_name)
            return f"{self._entry_name} (worker) S 1 0 0 0\n"

    class FakeEntry:
        def __init__(self, name: str) -> None:
            self.name = name

        def __truediv__(self, child: str) -> FakeStat:
            assert child == "stat"
            return FakeStat(self.name)

    class FakeProcRoot:
        def is_dir(self) -> bool:
            return True

        def iterdir(self) -> list[FakeEntry]:
            return [FakeEntry("100"), FakeEntry("101"), FakeEntry("102")]

    def monotonic() -> float:
        return 2.0 if accessed == ["100", "101"] else 0.0

    monkeypatch.setattr(process_module, "Path", lambda _path: FakeProcRoot())
    monkeypatch.setattr(process_module.time, "monotonic", monotonic)

    process_module._process_parent_map(deadline=1.0)

    assert accessed == ["100", "101"]


def test_run_captures_utf8_output_and_replaces_invalid_bytes(tmp_path: Path) -> None:
    result = ProcessRunner().run(
        [
            sys.executable,
            "-c",
            "import os, sys; print(os.getcwd(), flush=True); sys.stdout.buffer.write(b'\\xff\\n'); print('err', file=sys.stderr)",
        ],
        cwd=tmp_path,
    )

    assert result == CommandResult(
        argv=(
            sys.executable,
            "-c",
            "import os, sys; print(os.getcwd(), flush=True); sys.stdout.buffer.write(b'\\xff\\n'); print('err', file=sys.stderr)",
        ),
        cwd=str(tmp_path),
        returncode=0,
        stdout=f"{tmp_path}\n\ufffd\n",
        stderr="err\n",
    )


def test_run_writes_exact_input_bytes_to_stdin(tmp_path: Path) -> None:
    payload = b"prompt with no trailing newline\x00binary"

    result = ProcessRunner().run(
        [sys.executable, "-c", "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())"],
        cwd=tmp_path,
        input_bytes=payload,
    )

    assert result.stdout.encode("utf-8") == payload


def test_run_streams_stdout_to_binary_sink_without_retaining_it_in_memory(tmp_path: Path) -> None:
    payload_size = 2 * 1024 * 1024
    with tempfile.TemporaryFile("w+b") as sink:
        result = ProcessRunner().run(
            [sys.executable, "-c", f"import sys; sys.stdout.buffer.write(b'x' * {payload_size})"],
            cwd=tmp_path,
            stdout_sink=sink,
        )
        sink.seek(0, os.SEEK_END)
        assert sink.tell() == payload_size
    assert result.stdout == ""


def test_command_result_is_frozen() -> None:
    result = CommandResult(argv=("true",), cwd="/tmp", returncode=0, stdout="", stderr="")
    field_name = "returncode"

    with pytest.raises(FrozenInstanceError):
        setattr(result, field_name, 1)


@pytest.mark.parametrize(
    "argv",
    [
        [],
        [sys.executable, 1],
        [sys.executable, "bad\0argument"],
    ],
)
def test_run_rejects_invalid_argv(argv: list[object], tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        ProcessRunner().run(cast(Sequence[str], argv), cwd=tmp_path)


def test_run_raises_command_failed_with_result_for_nonzero_exit(tmp_path: Path) -> None:
    with pytest.raises(CommandFailed) as captured:
        ProcessRunner().run(
            [sys.executable, "-c", "import sys; print('nope', file=sys.stderr); raise SystemExit(7)"],
            cwd=tmp_path,
        )

    assert captured.value.result.returncode == 7
    assert captured.value.result.stderr == "nope\n"


def test_run_can_return_nonzero_result_when_check_is_false(tmp_path: Path) -> None:
    result = ProcessRunner().run(
        [sys.executable, "-c", "raise SystemExit(9)"],
        cwd=tmp_path,
        check=False,
    )

    assert result.returncode == 9


def test_run_raises_command_timed_out_with_partial_result(tmp_path: Path) -> None:
    with pytest.raises(CommandTimedOut) as captured:
        ProcessRunner().run(
            [
                sys.executable,
                "-c",
                "import sys, time; print('started', flush=True); print('waiting', file=sys.stderr, flush=True); time.sleep(5)",
            ],
            cwd=tmp_path,
            timeout=0.1,
        )

    assert captured.value.result.returncode == 124
    assert captured.value.result.stdout == "started\n"
    assert captured.value.result.stderr == "waiting\n"


def test_timeout_result_retains_and_redacts_partial_output(tmp_path: Path) -> None:
    secret = "timeout-secret"

    with pytest.raises(CommandTimedOut) as captured:
        ProcessRunner().run(
            [
                sys.executable,
                "-c",
                "import sys, time; print(sys.argv[1], flush=True); print(sys.argv[1], file=sys.stderr, flush=True); time.sleep(5)",
                secret,
            ],
            cwd=tmp_path,
            timeout=0.1,
            secrets=[secret],
        )

    rendered = repr(captured.value.result)
    assert secret not in rendered
    assert captured.value.result.stdout == "[REDACTED]\n"
    assert captured.value.result.stderr == "[REDACTED]\n"


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group behavior")
def test_timeout_kills_grandchild_process_group_and_reaps_launcher(tmp_path: Path) -> None:
    pid_file = tmp_path / "grandchild.pid"
    grandchild_script = "import time; time.sleep(30)"
    launcher_script = (
        "import pathlib, subprocess, sys, time; "
        "child = subprocess.Popen([sys.executable, '-c', sys.argv[2]]); "
        "pathlib.Path(sys.argv[1]).write_text(str(child.pid), encoding='utf-8'); "
        "print('launcher-ready', flush=True); "
        "time.sleep(30)"
    )
    grandchild_pid: int | None = None

    try:
        with pytest.raises(CommandTimedOut) as captured:
            ProcessRunner().run(
                [sys.executable, "-c", launcher_script, str(pid_file), grandchild_script],
                cwd=tmp_path,
                timeout=0.3,
            )

        assert captured.value.result.stdout == "launcher-ready\n"
        grandchild_pid = int(pid_file.read_text(encoding="utf-8"))
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            try:
                os.kill(grandchild_pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.02)
        else:
            pytest.fail(f"grandchild process {grandchild_pid} survived timeout")
    finally:
        if grandchild_pid is not None:
            try:
                os.kill(grandchild_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group behavior")
def test_cancel_terminates_only_the_process_group_created_by_this_run(tmp_path: Path) -> None:
    ready = tmp_path / "cancel-ready"
    cancel = threading.Event()
    failures: list[BaseException] = []
    sentinel = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        start_new_session=True,
    )

    def run() -> None:
        try:
            ProcessRunner().run(
                [
                    sys.executable,
                    "-c",
                    "import pathlib, sys, time; pathlib.Path(sys.argv[1]).touch(); time.sleep(30)",
                    str(ready),
                ],
                cwd=tmp_path,
                cancel_event=cancel,
            )
        except BaseException as error:  # noqa: BLE001 - the thread boundary returns the exact runner failure
            failures.append(error)

    runner = threading.Thread(target=run)
    runner.start()
    try:
        deadline = time.monotonic() + 2
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists()
        cancel.set()
        runner.join(timeout=2)

        assert not runner.is_alive()
        assert [type(error).__name__ for error in failures] == ["CommandCancelled"]
        assert sentinel.poll() is None
    finally:
        cancel.set()
        runner.join(timeout=2)
        if sentinel.poll() is None:
            os.killpg(sentinel.pid, signal.SIGKILL)
        sentinel.wait(timeout=2)


@pytest.mark.skipif(os.name != "posix", reason="POSIX descendant discovery behavior")
@pytest.mark.parametrize("stdio_mode", ["inherit", "devnull"])
def test_timeout_kills_descendant_that_escaped_into_new_session(
    tmp_path: Path,
    stdio_mode: str,
) -> None:
    pid_file = tmp_path / f"escaped-{stdio_mode}.pid"
    descendant_script = "import time; time.sleep(4)"
    launcher_script = (
        "import pathlib, subprocess, sys, time; "
        "kwargs = {} if sys.argv[3] == 'inherit' else "
        "{'stdout': subprocess.DEVNULL, 'stderr': subprocess.DEVNULL}; "
        "child = subprocess.Popen([sys.executable, '-c', sys.argv[2]], start_new_session=True, **kwargs); "
        "pathlib.Path(sys.argv[1]).write_text(str(child.pid), encoding='utf-8'); "
        "print('escaped-ready', flush=True); "
        "time.sleep(30)"
    )
    descendant_pid: int | None = None
    started = time.monotonic()

    try:
        with pytest.raises(CommandTimedOut) as captured:
            ProcessRunner().run(
                [sys.executable, "-c", launcher_script, str(pid_file), descendant_script, stdio_mode],
                cwd=tmp_path,
                timeout=0.3,
                secrets=["escaped-ready"],
            )

        elapsed = time.monotonic() - started
        assert elapsed < 2.5
        assert captured.value.result.stdout == "[REDACTED]\n"
        descendant_pid = int(pid_file.read_text(encoding="utf-8"))
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            try:
                os.kill(descendant_pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.02)
        else:
            pytest.fail(f"escaped descendant {descendant_pid} survived timeout")
    finally:
        if descendant_pid is not None:
            try:
                os.kill(descendant_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_run_raises_command_not_found_with_result(tmp_path: Path) -> None:
    missing = "powercontext-command-that-does-not-exist"

    with pytest.raises(CommandNotFound) as captured:
        ProcessRunner().run([missing], cwd=tmp_path)

    assert captured.value.result.returncode == 127
    assert captured.value.result.argv == (missing,)


def test_run_redacts_secrets_from_result_and_error_message(tmp_path: Path) -> None:
    short_secret = "token"
    long_secret = "token-with-detail"
    secret_cwd = tmp_path / f"workspace-{long_secret}"
    secret_cwd.mkdir()
    script = "import sys; print(sys.argv[1], file=sys.stderr); raise SystemExit(3)"

    with pytest.raises(CommandFailed) as captured:
        ProcessRunner().run(
            [sys.executable, "-c", script, long_secret],
            cwd=secret_cwd,
            secrets=["", short_secret, long_secret],
        )

    error = captured.value
    rendered = "\n".join(
        [
            str(error),
            repr(error),
            repr(error.result),
            error.result.stdout,
            error.result.stderr,
            *error.result.argv,
            error.result.cwd,
        ]
    )
    assert short_secret not in rendered
    assert long_secret not in rendered
    assert "[REDACTED]-with-detail" not in rendered
    assert error.result.argv[-1] == "[REDACTED]"
    assert error.result.stderr == "[REDACTED]\n"
    assert error.result.cwd.endswith("workspace-[REDACTED]")


def test_run_redacts_secret_from_not_found_exception(tmp_path: Path) -> None:
    secret = "sensitive-command"

    with pytest.raises(CommandNotFound) as captured:
        ProcessRunner().run([secret], cwd=tmp_path, secrets=[secret])

    assert secret not in str(captured.value)
    assert secret not in repr(captured.value)
    assert captured.value.result.argv == ("[REDACTED]",)


def test_run_inherits_only_allowlisted_environment_and_allows_explicit_overrides(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POWERCONTEXT_UNRELATED_SECRET", "must-not-leak")
    monkeypatch.setenv("HTTP_PROXY", "http://allowed.example")
    monkeypatch.setenv("LC_POWERCONTEXT_TEST", "allowed-locale")
    script = (
        "import os; "
        "print(os.environ.get('POWERCONTEXT_UNRELATED_SECRET', '<missing>')); "
        "print(os.environ['HTTP_PROXY']); "
        "print(os.environ['LC_POWERCONTEXT_TEST']); "
        "print(os.environ['EXPLICIT_VALUE'])"
    )

    result = ProcessRunner().run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env={"EXPLICIT_VALUE": "added-by-caller"},
    )

    assert result.stdout.splitlines() == [
        "<missing>",
        "[REDACTED]",
        "allowed-locale",
        "added-by-caller",
    ]


def test_run_automatically_redacts_proxy_credentials_from_output_and_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(key, raising=False)
    proxy = "http://proxy%2Duser:p%40ssword@proxy.example:7890"
    monkeypatch.setenv("HTTP_PROXY", proxy)
    script = (
        "import os, sys; "
        "from urllib.parse import unquote, urlsplit; "
        "value = os.environ['HTTP_PROXY']; parsed = urlsplit(value); "
        "print(value); print(parsed.username); print(parsed.password); "
        "print(unquote(parsed.username)); print(unquote(parsed.password), file=sys.stderr); "
        "raise SystemExit(5)"
    )

    with pytest.raises(CommandFailed) as captured:
        ProcessRunner().run([sys.executable, "-c", script], cwd=tmp_path)

    rendered = "\n".join(
        [
            str(captured.value),
            repr(captured.value),
            repr(captured.value.result),
            captured.value.result.stdout,
            captured.value.result.stderr,
        ]
    )
    for secret in (proxy, "proxy%2Duser", "p%40ssword", "proxy-user", "p@ssword"):
        assert secret not in rendered
    assert "[REDACTED]" in rendered


def test_run_automatically_redacts_encoded_and_decoded_proxy_credentials_on_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(key, raising=False)
    proxy = "http://proxy%2Duser:p%40ssword@proxy.example:7890"
    monkeypatch.setenv("https_proxy", proxy)
    script = (
        "import os; "
        "from urllib.parse import unquote, urlsplit; "
        "value = os.environ['https_proxy']; parsed = urlsplit(value); "
        "print(value); print(parsed.username); print(parsed.password); "
        "print(unquote(parsed.username)); print(unquote(parsed.password))"
    )

    result = ProcessRunner().run([sys.executable, "-c", script], cwd=tmp_path)

    rendered = repr(result)
    for secret in (proxy, "proxy%2Duser", "p%40ssword", "proxy-user", "p@ssword"):
        assert secret not in rendered
    assert result.stdout.splitlines() == ["[REDACTED]"] * 5
