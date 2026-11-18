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

from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from powercontext.source_management import remote_deployment
from powercontext.source_management.deployment import credential
from powercontext.source_management.durable import RuntimeOwner

HOLD = 0.3


@contextmanager
def held(directory):
    """Hold the lock the way a running process or an older release does, and release it after HOLD seconds."""
    owner = RuntimeOwner(directory)
    timer = threading.Timer(HOLD, owner.close)
    timer.start()
    try:
        yield time.monotonic()
    finally:
        timer.join()


def test_credential_waits_for_a_concurrent_configuration_writer(tmp_path):
    with held(tmp_path / "configuration-lock") as started:
        path = credential(tmp_path, "deployer", "pc-source-deployer")
        waited = time.monotonic() - started

    identities = json.loads((tmp_path / "identities.json").read_text())["identities"]
    assert waited >= HOLD
    assert path == tmp_path / "deployer.token"
    assert [identity["credential_id"] for identity in identities] == ["deployer"]


def test_concurrent_credentials_are_all_recorded(tmp_path):
    names = [f"execution-{index}" for index in range(8)]
    with ThreadPoolExecutor(len(names)) as pool:
        list(pool.map(lambda name: credential(tmp_path, name, "pc-source-" + name), names))

    identities = json.loads((tmp_path / "identities.json").read_text())["identities"]
    assert sorted(identity["credential_id"] for identity in identities) == sorted(names)


def test_remote_load_waits_for_a_concurrent_load(tmp_path):
    config = tmp_path / "instance.yaml"
    config.write_text("")
    server = SimpleNamespace(credential_file="missing.token")
    local = SimpleNamespace(deployment=SimpleNamespace(state_dir="state", server=server))

    with held(tmp_path / "state" / "deployment-lock") as started:
        with pytest.raises(ValueError, match="Credential must be a regular protected file"):
            remote_deployment.load(config, local)
        waited = time.monotonic() - started

    assert waited >= HOLD
