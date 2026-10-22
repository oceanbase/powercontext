"""Process inventory is observed from the OS, independently of configured worker slots."""

from __future__ import annotations

import subprocess
import sys
import threading

from prometheus_client.parser import text_string_to_metric_families

from powercontext.server.metrics import ServerMetrics


def _values(metrics):
    return {
        sample.name: sample.value
        for family in text_string_to_metric_families(metrics.render().decode())
        for sample in family.samples
        if sample.name.startswith("powercontext_server_process_") and not sample.labels
    }


def test_inventory_observes_a_real_child_and_thread_without_exporting_process_identity():
    metrics = ServerMetrics()
    before = _values(metrics)
    assert before["powercontext_server_process_os_threads"] >= 1
    assert before["powercontext_server_process_python_threads"] >= 1
    release = threading.Event()
    ready = threading.Event()

    def hold():
        ready.set()
        release.wait(10)

    thread = threading.Thread(target=hold)
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(15)"])
    thread.start()
    try:
        assert ready.wait(2)
        observed = _values(metrics)
        assert (
            observed["powercontext_server_process_python_threads"]
            >= before["powercontext_server_process_python_threads"] + 1
        )
        assert (
            observed["powercontext_server_process_os_threads"] >= observed["powercontext_server_process_python_threads"]
        )
        assert observed["powercontext_server_process_direct_children"] >= 1
        assert (
            observed["powercontext_server_process_descendants"]
            >= observed["powercontext_server_process_direct_children"]
        )
        assert observed["powercontext_server_process_descendant_threads"] >= 1
        rendered = metrics.render().decode()
        assert "pid=" not in rendered and "command=" not in rendered
    finally:
        release.set()
        thread.join(2)
        process.terminate()
        process.wait(timeout=5)
