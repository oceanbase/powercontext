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

"""Test-owned evidence captured synchronously before worker cleanup."""

import asyncio
import os
import sys
from contextlib import suppress
from pathlib import Path
from types import CoroutineType, GeneratorType

from powercontext.builtin.runtime.artifact_processing import _SpawnedWorkerHandle


def _location(frame):
    # No source lines, absolute paths or locals: those may contain credentials or content.
    return f"{Path(frame.f_code.co_filename).name}:{frame.f_lineno} in {frame.f_code.co_name}"


def _task_state(task):
    if task is None:
        return "not_started"
    if not isinstance(task, asyncio.Task):
        return "unknown"
    return f"{id(task)} done={task.done()} cancelled={task.cancelled()}"


def _task_lines(task, handles):
    lines = [f"task={_task_state(task)}"]
    awaited = task.get_coro()
    while isinstance(awaited, (CoroutineType, GeneratorType)):
        if isinstance(awaited, CoroutineType):
            frame, awaited = awaited.cr_frame, awaited.cr_await
        else:
            frame, awaited = awaited.gi_frame, awaited.gi_yieldfrom
        if frame is not None:
            lines.append(f"  {_location(frame)}")
            # Handles live in supervisor/handle coroutine frames, not on the app.
            # Inspect only these two bindings for identity; never render their values.
            for name in ("self", "handle"):
                candidate = frame.f_locals.get(name)
                if isinstance(candidate, _SpawnedWorkerHandle):
                    handles[id(candidate)] = candidate
    if awaited is not None:
        lines.append(f"  await boundary={type(awaited).__name__}")
    return lines


def _proc_state(pid):
    state = "state=unknown ppid=unknown utime_ticks=unknown stime_ticks=unknown"
    wchan = "unknown"
    if isinstance(pid, int):
        directory = Path("/proc") / str(pid)
        with suppress(OSError, ValueError, IndexError):
            # Field 2 (comm) can contain spaces/parentheses and is deliberately omitted.
            fields = (directory / "stat").read_text().rsplit(")", 1)[1].split()
            state = (
                f"state={fields[0]} ppid={int(fields[1])} utime_ticks={int(fields[11])} stime_ticks={int(fields[12])}"
            )
        with suppress(OSError):
            wchan = (directory / "wchan").read_text().strip() or "unknown"
            if wchan == "0":
                wchan = "unknown"
    return f"proc {state} wchan={wchan}"


def _handle_lines(handle):
    process = getattr(handle, "_process", None)
    popen = getattr(process, "_popen", None)
    # BaseProcess.exitcode/is_alive and repr can reap a child. Only read the cache.
    pid = getattr(popen, "pid", None)
    returncode = getattr(popen, "returncode", None)
    lock = getattr(handle, "_close_lock", None)
    locked = lock.locked() if isinstance(lock, asyncio.Lock) else "unknown"
    return [
        f"worker={id(handle)} name={getattr(process, 'name', 'unknown')} pid={pid if pid is not None else 'unknown'} "
        f"cached_returncode={returncode if returncode is not None else 'unknown'} "
        f"closed={getattr(handle, '_closed', 'unknown')} close_lock_locked={locked} "
        f"join_task={_task_state(getattr(handle, '_join_task', 'unknown'))}",
        f"  {_proc_state(pid)}",
    ]


def record_worker_timeout(request, tmp_path):
    """Retain a read-only snapshot; diagnostic failures must not replace the timeout."""
    lines = [f"Worker timeout snapshot before lifespan cleanup parent_pid={os.getpid()}", "parent threads (no locals):"]
    try:
        for thread_id, frame in sorted(sys._current_frames().items()):
            lines.append(f"thread={thread_id}")
            while frame is not None:
                lines.append(f"  {_location(frame)}")
                frame = frame.f_back
        executor = getattr(asyncio.get_running_loop(), "_default_executor", None)
        threads = getattr(executor, "_threads", None)
        queued = "unknown"
        if executor is not None:
            with suppress(Exception):
                queued = executor._work_queue.qsize()
        lines.append(f"executor threads={len(threads) if threads is not None else 'unknown'} queued={queued}")
        lines.append("asyncio task await chains (no locals):")
        handles = {}
        for task in sorted(asyncio.all_tasks(), key=id):
            lines.extend(_task_lines(task, handles))
        lines.append("spawned worker handles:")
        if not handles:
            lines.append("unknown (no handle discoverable in task await chains)")
        for handle in handles.values():
            lines.extend(_handle_lines(handle))
    except Exception as error:
        # Exception messages can contain sensitive values; retain only the type.
        lines.append(f"snapshot incomplete: {type(error).__name__}")
    snapshot = "\n".join(lines) + "\n"
    with suppress(Exception):
        request.node.add_report_section("call", "worker timeout", snapshot)
    with suppress(Exception):
        directory = tmp_path / "logs"
        directory.mkdir(exist_ok=True)
        (directory / "worker-timeout.log").write_text(snapshot, encoding="utf-8")
