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

from alembic import context

from powercontext.builtin.persistence.migration import SCHEMA_VERSION_TABLE


def run_migrations() -> None:
    connection = context.config.attributes.get("connection")
    if connection is None:
        raise RuntimeError("PowerContext migrations require a caller-owned connection")  # noqa: TRY003
    context.configure(
        connection=connection,
        target_metadata=None,
        version_table=SCHEMA_VERSION_TABLE,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


run_migrations()
