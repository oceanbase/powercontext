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

"""Content-free, instantaneous OS process inventory for the owning Server."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from contextlib import suppress

import psutil
from prometheus_client.core import GaugeMetricFamily, Metric

_MEASUREMENTS = {
    "os_threads": "OS threads in the parent Server process.",
    "python_threads": "Live Python threading objects in the parent Server process.",
    "direct_children": "Direct child processes, including multiprocessing infrastructure.",
    "descendants": "All descendant processes, including multiprocessing infrastructure.",
    "descendant_threads": "OS threads in the observed descendant processes.",
}


class ProcessInventoryCollector:
    """Read an OS snapshot; inaccessible measurements are unknown, never healthy zero."""

    def collect(self) -> Iterator[Metric]:
        values = {"python_threads": threading.active_count()}
        try:
            parent = psutil.Process()
            with suppress(psutil.Error, OSError, NotImplementedError):
                values["os_threads"] = parent.num_threads()
            children = parent.children(recursive=True)
            direct = threads = 0
            for child in children:
                with child.oneshot():
                    direct += child.ppid() == parent.pid
                    threads += child.num_threads()
            values.update(direct_children=direct, descendants=len(children), descendant_threads=threads)
        except (psutil.Error, OSError, NotImplementedError):
            # A process can disappear during enumeration. Keep parent readings,
            # while making the incomplete tree observation explicit.
            pass
        supported = GaugeMetricFamily(
            "powercontext_server_process_inventory_supported",
            "Whether this scrape observed the specified process measurement.",
            labels=["measurement"],
        )
        observed = GaugeMetricFamily(
            "powercontext_server_process_inventory_observed_timestamp_seconds",
            "Unix time of the latest successful instantaneous process reading.",
            labels=["measurement"],
        )
        now = time.time()
        for name, description in _MEASUREMENTS.items():
            supported.add_metric([name], int(name in values))
            if name in values:
                yield GaugeMetricFamily("powercontext_server_process_" + name, description, value=values[name])
                observed.add_metric([name], now)
        yield supported
        yield observed
