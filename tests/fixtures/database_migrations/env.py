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

"""Run a revision on the executor's pinned connection; never open another engine."""

from alembic import context

connection = context.config.attributes["connection"]
context.configure(
    connection=connection,
    version_table="pc_schema_revision",
    transactional_ddl=connection.dialect.name == "sqlite",
)
with context.begin_transaction():
    context.run_migrations()
