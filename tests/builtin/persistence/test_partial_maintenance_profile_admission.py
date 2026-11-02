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

"""Unsupported partial vector maintenance must fail before any durable work."""

import pytest
from fastapi.testclient import TestClient

from powercontext.builtin.persistence.migrations.deployment import deployment_runner
from powercontext.builtin.persistence.migrations.models import MigrationError
from powercontext.server.factory import create_server_app
from tests.builtin.persistence.test_release140_server_migrations import ControlledEmbedding, _apply, _source


def _evidence(runner):
    directory = runner.evidence_path.parent
    return {path.relative_to(directory): path.read_bytes() for path in directory.rglob("*") if path.is_file()}


@pytest.mark.parametrize("release", ["1.3.0", "1.4.0"])
@pytest.mark.parametrize("bundle", ["atomic-projection", "processing"])
def test_missing_configuration_cannot_admit_partial_vector_maintenance(tmp_path, release, bundle):
    settings, profile, full = _source(tmp_path, release=release, vectors=True)
    _apply(full)
    with TestClient(
        create_server_app(
            settings=settings,
            embedding_model=ControlledEmbedding(profile),
            scheduler_path=tmp_path / "scheduler",
        )
    ) as client:
        response = client.post(
            "/v1/memory/remember",
            json={"scope_id": "scope", "kind": "fact", "text": "Preserve this healthy vector projection."},
        )
        assert response.status_code == 200, response.text
    assert full.verify().state == "ready"
    before = full.database.read_bytes(), _evidence(full)

    # The caller omits embedding configuration. Admission must inspect the
    # stored declaration, not treat this database as a no-vector deployment.
    partial = deployment_runner(settings, bundle=bundle)
    for operation in (
        partial.plan,
        partial.verify,
        lambda: partial.apply(
            plan_id="unsupported-plan",
            accepted=True,
            maintenance_confirmed=True,
            backup_policy="skip",
            accept_no_backup=True,
        ),
    ):
        with pytest.raises(MigrationError) as error:
            operation()
        assert error.value.code == "unsupported_upgrade_configuration"
        assert (full.database.read_bytes(), _evidence(full)) == before

    # A rejected command must not leave an active operation that prevents the
    # next supported full maintenance command from reading the original state.
    reopened = deployment_runner(settings, embedding_profile=profile)
    assert reopened.plan().state == "ready"
    assert reopened.verify().state == "ready"
    assert not _apply(reopened).changed
    assert (full.database.read_bytes(), _evidence(full)) == before
