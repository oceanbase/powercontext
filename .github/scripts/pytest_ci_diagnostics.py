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

"""Emit existing pytest failure reports from the controller without changing tests."""

import datetime
import json
import os
import shutil
from pathlib import Path

import pytest


def pytest_configure(config):
    if not hasattr(config, "workerinput"):
        config.pluginmanager.register(_FailureProbe(config), "ci-immediate-failure-probe")


class _FailureProbe:
    def __init__(self, config):
        self.config = config

    def pytest_runtest_logreport(self, report):
        if not report.failed:
            return
        text = report.longreprtext
        record = {
            "observed_at": datetime.datetime.now(datetime.UTC).isoformat(),
            "nodeid": report.nodeid,
            "when": report.when,
            "duration_seconds": report.duration,
            "worker_id": getattr(report, "worker_id", None),
            "longrepr": text,
            "sections": report.sections,
        }
        directory = Path(os.environ["RUNNER_TEMP"]) / "pytest-diagnostics"
        directory.mkdir(parents=True, exist_ok=True)
        with (directory / "failures.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
            stream.flush()
        terminal = self.config.pluginmanager.getplugin("terminalreporter")
        terminal.write_sep("=", f"CI immediate failure: {report.nodeid} [{report.when}]")
        terminal.write_line(text)
        terminal.flush()


_CAPTURED_LOG_FIELDS = (
    "event",
    "error_code",
    "error_category",
    "stage",
    "binding",
    "duration_ms",
    "outcome",
    "error_type",
    "exception_type",
    "queue_wait_ms",
    "family",
)


def _retain_seekdb_native_logs(item, report):
    destination = os.environ.get("POWERCONTEXT_TEST_SEEKDB_DIAGNOSTICS")
    root = item.funcargs.get("short_tmp_path")
    if (
        report.when not in {"setup", "call"}
        or os.environ.get("POWERCONTEXT_TEST_MIGRATION_SEEKDB") != "1"
        or not destination
        or not isinstance(root, Path)
    ):
        return
    try:
        root = root.resolve()
        retained = Path(destination).resolve() / root.name
        if retained.is_relative_to(root):
            report.sections.append((
                "CI SeekDB native log preservation",
                "Capture directory is inside the test database",
            ))
            return
        count = 0
        # Preserve every existing rotation before short_tmp_path removes the
        # failed instance; a later workflow archive can otherwise be empty.
        for source in root.rglob("seekdb.log*"):
            if source.is_symlink() or not source.is_file() or not source.resolve().is_relative_to(root):
                continue
            target = retained / source.relative_to(root)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            count += 1
        if count:
            report.sections.append(("CI retained SeekDB native logs", f"Preserved {count} native log files"))
    except (OSError, ValueError) as error:
        report.sections.append(("CI SeekDB native log preservation", f"Capture failed: {type(error).__name__}"))


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item, call):
    report = yield
    if not report.failed:
        return report
    _retain_seekdb_native_logs(item, report)
    try:
        from _pytest.logging import caplog_records_key

        records = item.stash.get(caplog_records_key, {}).get(report.when, [])
        captured = []
        for record in records:
            fields = {"level": record.levelname, "name": record.name, "message": record.getMessage()}
            for key in _CAPTURED_LOG_FIELDS:
                value = getattr(record, key, None)
                if hasattr(record, key) and (value is None or isinstance(value, (str, int, float, bool))):
                    fields[key] = value
            captured.append(fields)
        if captured:
            report.sections.append(("CI structured captured logs", json.dumps(captured, ensure_ascii=False)))
    except Exception:
        # Pytest versions without this stash simply keep their original report.
        return report
    return report
