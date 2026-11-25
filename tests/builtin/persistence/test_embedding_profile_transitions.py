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

"""A managed profile switch is journaled beside the published release checksums.

The journal is the only state these tests touch: they prove that accepting a
switch never rewrites the release map and that resume and admission both read
the transition chain. Opening a real embedded backend is covered separately.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from powercontext.builtin.persistence.migrations.connections import BackendIdentity, MaintenanceConnections
from powercontext.builtin.persistence.migrations.deployment import server_bundle
from powercontext.builtin.persistence.migrations.models import MigrationError
from powercontext.builtin.persistence.migrations.mysql import MySQLMigrationRunner, _Evidence, _Journal
from powercontext.builtin.persistence.migrations.profile_transitions import verify_embedding_profile_evidence
from powercontext.builtin.persistence.seekdb import SeekDBConfig
from tests.builtin.persistence.test_topic_memory_reindex import _profile


def _journaled_runner(tmp_path: Path, profile) -> tuple[BackendIdentity, _Journal, MySQLMigrationRunner]:
    connections = MaintenanceConnections(
        SeekDBConfig(path=tmp_path / "seekdb"), evidence_directory=tmp_path / "evidence"
    )
    identity = connections.journal_identity()
    assert identity is not None
    runner = MySQLMigrationRunner(
        connections,
        server_bundle(embedding_profile=profile),
        evidence_directory=tmp_path / "evidence",
    )
    return identity, _Journal(tmp_path / "evidence", identity), runner


def _completed(identity: BackendIdentity, runner: MySQLMigrationRunner) -> _Evidence:
    return _Evidence(
        state="complete",
        database_id=identity.database_id,
        source_revision=None,
        source_fingerprint="source",
        bundle_checksum=runner.checksum,
        configuration_digest="configuration",
        last_revision=runner.bundle.head,
        maintenance_window_id="window",
        backup_policy="skip",
        backup_state="not_required",
        release_checksums=dict(runner.checksums),
    )


def _chain(evidence: _Evidence) -> list[tuple[str, str, str]]:
    return [
        (item.previous.profile_id, item.target.profile_id, item.state)
        for item in evidence.embedding_profile_transitions
    ]


def _verify(evidence: _Evidence, runner: MySQLMigrationRunner, profile) -> None:
    verify_embedding_profile_evidence(
        evidence.model_dump(mode="json"),
        profile=profile,
        checksums=runner._checksums_for_profile(profile),
        checksums_for=runner._checksums_for_profile,
    )


def test_profile_switch_preserves_releases_and_admits_the_declared_profile(tmp_path: Path) -> None:
    old_profile, new_profile, newer_profile = _profile("old", 2), _profile("new", 3), _profile("newer", 4)
    identity, journal, initial_runner = _journaled_runner(tmp_path, old_profile)
    journal.write(_completed(identity, initial_runner))
    recorded = dict(initial_runner.checksums)

    _new_identity, journal, runner = _journaled_runner(tmp_path, new_profile)
    assert runner.begin_embedding_profile_transition(old_profile) is True

    pending = journal.read()
    assert pending is not None
    assert pending.release_checksums == recorded
    assert _chain(pending) == [(old_profile.profile_id, new_profile.profile_id, "pending")]
    # Activation may commit before the completion is journaled, so both the
    # declared target and the still-declared previous profile must be admitted.
    _verify(pending, runner, new_profile)
    _verify(pending, runner, old_profile)
    assert runner.begin_embedding_profile_transition(old_profile) is False

    assert runner.complete_embedding_profile_transition() is True
    completed = journal.read()
    assert completed is not None
    assert completed.release_checksums == recorded
    assert _chain(completed) == [(old_profile.profile_id, new_profile.profile_id, "complete")]
    _verify(completed, runner, new_profile)
    with pytest.raises(MigrationError, match="do not reach the configured profile"):
        _verify(completed, runner, old_profile)
    assert runner.complete_embedding_profile_transition() is False

    _newer_identity, journal, newer_runner = _journaled_runner(tmp_path, newer_profile)
    assert newer_runner.begin_embedding_profile_transition(new_profile) is True
    assert newer_runner.begin_embedding_profile_transition(new_profile) is False
    chained = journal.read()
    assert chained is not None
    assert chained.release_checksums == recorded
    assert _chain(chained) == [
        (old_profile.profile_id, new_profile.profile_id, "complete"),
        (new_profile.profile_id, newer_profile.profile_id, "pending"),
    ]
    _verify(chained, newer_runner, newer_profile)


def test_a_pending_switch_blocks_an_unrelated_previous_profile(tmp_path: Path) -> None:
    old_profile, new_profile, other_profile = _profile("old", 2), _profile("new", 3), _profile("other", 4)
    identity, journal, initial_runner = _journaled_runner(tmp_path, old_profile)
    journal.write(_completed(identity, initial_runner))

    _identity, _journal, runner = _journaled_runner(tmp_path, new_profile)
    runner.begin_embedding_profile_transition(old_profile)
    _other_identity, _other_journal, other_runner = _journaled_runner(tmp_path, other_profile)

    with pytest.raises(MigrationError, match="Another embedding profile switch is pending"):
        other_runner.begin_embedding_profile_transition(other_profile)
