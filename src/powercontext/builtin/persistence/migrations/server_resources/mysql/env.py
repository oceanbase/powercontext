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

"""Advance the standard Alembic head only after a registered DDL postcondition."""

from alembic import context

context.configure(
    connection=context.config.attributes["connection"],
    version_table="pc_schema_revision",
    transactional_ddl=False,
    transaction_per_migration=True,
)
with context.begin_transaction():
    context.run_migrations()
