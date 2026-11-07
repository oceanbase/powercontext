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

"""Require completed portable restores before admitting managed Server storage."""

from powercontext.builtin.persistence.migrations.models import MigrationError


def preflight(connection, *, configuration):
    """Earlier registered layouts have no portable restore receipts."""


def apply(connection, *, configuration):
    """Receipt creation and timestamp widening require no data rewrite."""


def verify(connection, *, configuration):
    validate(connection, configuration=configuration)


def validate(connection, *, configuration):
    pending = connection.exec_driver_sql(
        "SELECT 1 FROM pc_portable_restore_receipts WHERE projections_ready = 0 LIMIT 1"
    ).first()
    if pending is not None:
        raise MigrationError(
            "archive_recovery_required",
            "Portable restore projections are incomplete; finish archive restore before serving requests.",
        )
