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

"""Sample host counters during one diagnostic job; never alter resource controls."""

import hashlib
import json
import os
import time
from pathlib import Path


def read_cgroup():  # noqa: C901 - cgroup v1 and v2 expose different optional counter files.
    """Read this process's mounted cgroup; absent optional counters are harmless."""
    result = {}
    try:
        memberships = {}
        for line in Path("/proc/self/cgroup").read_text().splitlines():
            _, controllers, group = line.split(":", 2)
            for controller in controllers.split(","):
                memberships[controller] = group
        mounts = Path("/proc/self/mountinfo").read_text().splitlines()
    except (OSError, ValueError):
        return result
    for line in mounts:
        before, separator, after = line.partition(" - ")
        fields, filesystem = before.split(), after.split()
        if not separator or len(fields) < 5 or len(filesystem) < 3:
            continue
        if filesystem[0] == "cgroup2":
            group = memberships.get("")
            names = ("cpu.stat", "cpu.max", "memory.current", "memory.peak", "memory.events")
            version = 2
        elif filesystem[0] == "cgroup":
            controllers = set(filesystem[2].split(","))
            controller = next((name for name in ("cpuacct", "cpu", "memory") if name in controllers), None)
            group = memberships.get(controller)
            names = (
                "cpuacct.usage",
                "cpuacct.stat",
                "cpu.stat",
                "cpu.cfs_quota_us",
                "cpu.cfs_period_us",
                "memory.usage_in_bytes",
                "memory.max_usage_in_bytes",
                "memory.failcnt",
                "memory.oom_control",
            )
            version = 1
        else:
            continue
        if group is None:
            continue

        # mountinfo escapes spaces, tabs, newlines and backslashes with octal codes.
        def unescape(value):
            for code, character in (("040", " "), ("011", "\t"), ("012", "\n"), ("134", "\\")):
                value = value.replace("\\" + code, character)
            return value

        root, mount = Path(unescape(fields[3])), Path(unescape(fields[4]))
        try:
            relative = Path(group).relative_to(root) if group != "/" else Path(".")
        except ValueError:
            continue
        directory = mount / relative
        result["version"] = version
        for name in names:
            try:
                value = (directory / name).read_text().strip()
                if name == "cpu.max":
                    result[name] = value
                elif name.endswith((".stat", ".events", ".oom_control")):
                    result[name] = {key: int(number) for key, number in (line.split() for line in value.splitlines())}
                else:
                    result[name] = int(value)
            except (OSError, ValueError):
                continue
    return result


out = Path(os.environ["RUNNER_TEMP"]) / "pytest-diagnostics"
out.mkdir(parents=True, exist_ok=True)
started = time.monotonic()
previous = None
boot = hashlib.sha256(Path("/proc/sys/kernel/random/boot_id").read_bytes()).hexdigest()[:16]
with (out / "host-samples.jsonl").open("a", buffering=1) as stream:
    while not (out / "sampling-stop").exists() and time.monotonic() - started < 3600:
        now = time.time()
        monotonic_ns = time.monotonic_ns()
        try:
            lines = Path("/proc/stat").read_text().splitlines()
            counters = list(map(int, lines[0].split()[1:9]))
            cores = sum(line.split()[0][3:].isdigit() for line in lines if line.startswith("cpu"))
            memory = {}
            for line in Path("/proc/meminfo").read_text().splitlines():
                name, value = line.split(":", 1)
                if name in {"MemTotal", "MemAvailable", "SwapTotal", "SwapFree"}:
                    memory[name] = int(value.split()[0]) * 1024
            row = {
                "time_unix": now,
                "monotonic": monotonic_ns / 1e9,
                "monotonic_ns": monotonic_ns,
                "node_boot_hash": boot,
                "cpu_count": cores,
                "loadavg": list(map(float, Path("/proc/loadavg").read_text().split()[:3])),
                "cpu_ticks": counters,
                "memory_bytes": memory,
                "cgroup": read_cgroup(),
            }
            if previous is not None:
                delta = [a - b for a, b in zip(counters, previous, strict=True)]
                total = sum(delta)
                if total > 0 and all(value >= 0 for value in delta):
                    row["busy_cpu_cores"] = cores * sum(delta[i] for i in (0, 1, 2, 5, 6)) / total
                    row["iowait_cpu_cores"] = cores * delta[4] / total
                    row["steal_cpu_cores"] = cores * delta[7] / total
                else:
                    row["cpu_interval_valid"] = False
            previous = counters
            stream.write(json.dumps(row) + "\n")
        except Exception as error:
            stream.write(
                json.dumps({
                    "time_unix": now,
                    "monotonic": monotonic_ns / 1e9,
                    "monotonic_ns": monotonic_ns,
                    "error_type": type(error).__name__,
                })
                + "\n"
            )
        time.sleep(2)
