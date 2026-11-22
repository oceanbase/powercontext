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

"""Exercise released-wheel verification and dependency installation routing."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import capture_release_schema
import pytest
import upgrade_release_gate

from tests.release_upgrades import test_full_server

MIRROR = "https://mirrors.aliyun.com/pypi/simple/"
pytestmark = pytest.mark.skipif(os.name == "nt", reason="The installer probe uses POSIX executables")


class InstallationFinished(Exception):
    """Stop at the boundary before running the installed Server."""


@pytest.fixture
def capture_installation(tmp_path, monkeypatch):
    work = tmp_path / "isolated release"
    work.mkdir()
    wheel = tmp_path / "powercontext-1.4.0-py3-none-any.whl"
    wheel.write_bytes(b"released wheel fixture")
    checksum = hashlib.sha256(wheel.read_bytes()).hexdigest()
    metadata = {
        "info": {"version": "1.4.0"},
        "urls": [
            {
                "filename": wheel.name,
                "url": f"https://files.pythonhosted.org/packages/fixture/{wheel.name}",
                "digests": {"sha256": checksum},
                "upload_time_iso_8601": "2026-10-19T05:59:50Z",
            }
        ],
    }
    metadata_path = tmp_path / "pypi.json"
    metadata_path.write_text(json.dumps(metadata))
    record = tmp_path / "commands.jsonl"
    uv = tmp_path / "bin" / "uv"
    uv.parent.mkdir()
    uv.write_text(
        f"#!{sys.executable}\n"
        + """import hashlib, json, os, sys
from pathlib import Path
args = sys.argv[1:]
index = os.environ.get('UV_INDEX')
value = {
    'args': args,
    'index': index,
    'inherited': {key: os.environ.get(key) for key in ('PIP_INDEX_URL', 'PYTHONPATH', 'POWERCONTEXT_TEST')},
}
if args[:2] == ['pip', 'install']:
    value['wheel_sha256'] = hashlib.sha256(Path(args[-1].split('[', 1)[0]).read_bytes()).hexdigest()
with open(os.environ['CAPTURE_COMMANDS'], 'a') as output:
    output.write(json.dumps(value) + '\\n')
if index and os.environ.get('CAPTURE_FAIL_MIRROR') == '1':
    sys.exit(1)
"""
    )
    uv.chmod(0o755)
    for key, value in {
        "PATH": f"{uv.parent}{os.pathsep}{os.environ['PATH']}",
        "RUNNER_OS": "Linux",
        "RUNNER_ENVIRONMENT": "self-hosted",
        "PC_CI_PYTHON_INSTALLER": str(Path(__file__).parents[1] / "scripts/ci_python_mirror.py"),
        "PC_CI_PYTHON_MIRROR": MIRROR,
        "CAPTURE_COMMANDS": str(record),
        "CAPTURE_FAIL_MIRROR": "0",
        "UV_INDEX": "https://inherited.invalid/simple",
        "PIP_INDEX_URL": "https://inherited.invalid/simple",
        "PYTHONPATH": str(tmp_path / "unrelated runtime"),
        "POWERCONTEXT_TEST": "unrelated runtime configuration",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(upgrade_release_gate, "case_workspace", lambda _backend, _output: nullcontext(work))
    original_command = test_full_server.command

    def command(arguments, environment, directory, log):
        if log.name == "installed.log":
            raise InstallationFinished
        return original_command(arguments, environment, directory, log)

    monkeypatch.setattr(test_full_server, "command", command)
    args = SimpleNamespace(
        backend="oceanbase",
        configuration="full-server-vector",
        output=tmp_path / "capture",
        pypi_metadata=metadata_path,
        wheel=wheel,
    )
    return SimpleNamespace(
        args=args, work=work, checksum=checksum, metadata=metadata, metadata_path=metadata_path, record=record
    )


@pytest.mark.parametrize(
    ("changes", "expected_indexes"),
    [
        ({}, [MIRROR]),
        ({"CAPTURE_FAIL_MIRROR": "1"}, [MIRROR, None]),
        ({"RUNNER_ENVIRONMENT": "github-hosted"}, [None]),
        ({"RUNNER_OS": "macOS"}, [None]),
        ({"PC_CI_PYTHON_INSTALLER": ""}, [None]),
        ({"PC_CI_PYTHON_MIRROR": ""}, [None]),
    ],
)
def test_capture_installs_verified_release_with_selected_transport(
    capture_installation, monkeypatch, changes, expected_indexes
):
    fixture = capture_installation
    for key, value in changes.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(InstallationFinished):
        capture_release_schema.capture(fixture.args)
    calls = [json.loads(line) for line in fixture.record.read_text().splitlines()]
    installs = [call for call in calls if call["args"][:2] == ["pip", "install"]]
    assert [call["index"] for call in installs] == expected_indexes
    official = expected_indexes == [None]
    for call in installs:
        assert call["args"] == [
            "pip",
            "install",
            "--python",
            str(fixture.work / "source/bin/python"),
            "--no-config",
            *(["--default-index", "https://pypi.org/simple"] if official else []),
            f"{fixture.work / fixture.args.wheel.name}[server,cli,code]",
        ]
        assert call["wheel_sha256"] == fixture.checksum
        assert not any(call["inherited"].values())
    manifest = json.loads((fixture.args.output / "manifest.json").read_text())
    assert manifest["status"] == "incomplete"
    assert manifest["source_wheel"]["sha256"] == fixture.checksum
    assert manifest["source_wheel"]["url"] == fixture.metadata["urls"][0]["url"]
    assert manifest["source_wheel"]["metadata_url"] == "https://pypi.org/pypi/powercontext/1.4.0/json"
    assert manifest["source_wheel"]["metadata_origin"] == "provided-pypi-json"


@pytest.mark.parametrize("invalid", ["wheel-hash", "wheel-origin"])
def test_capture_rejects_unverified_subject_before_mirror_installation(capture_installation, invalid):
    fixture = capture_installation
    if invalid == "wheel-hash":
        fixture.args.wheel.write_bytes(b"modified release wheel")
        message = "Source wheel does not match the PyPI SHA256"
    else:
        fixture.metadata["urls"][0]["url"] = f"{MIRROR}{fixture.args.wheel.name}"
        fixture.metadata_path.write_text(json.dumps(fixture.metadata))
        message = "Expected a wheel hosted by PyPI"
    with pytest.raises(ValueError, match=message):
        capture_release_schema.capture(fixture.args)
    assert not fixture.record.exists()
