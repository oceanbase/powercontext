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

"""Initialize current Server fixtures before SDK or injected access components write."""

from pathlib import Path

from powercontext.builtin.artifacts.memory import EmbeddingProfile
from powercontext.builtin.persistence.migrations.deployment import deployment_runner
from powercontext.server.settings import ServerSettings


def prepare_server_database(
    settings: ServerSettings,
    *,
    evidence_dir: Path | None = None,
    embedding_profile: EmbeddingProfile | None = None,
) -> None:
    runner = deployment_runner(
        settings,
        evidence_dir=evidence_dir,
        lock_coordination="single-host" if settings.database.kind == "oceanbase" else None,
        embedding_profile=embedding_profile,
    )
    plan = runner.plan(backup_policy="skip")
    assert plan.state in {"uninitialized", "ready"}
    result = runner.apply(
        plan_id=plan.plan_id,
        backup_policy="skip",
        accepted=True,
        maintenance_confirmed=True,
        accept_no_backup=True,
    )
    assert result.state == "ready"
    assert result.readiness_scope == "server"
