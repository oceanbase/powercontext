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

"""Versioned, explicitly invoked migrations.

Nothing in this package runs during startup and nothing here is a startup backfill. Startup
convergence is a separate mechanism owned by the Runtime: it applies additive schema adjustments
and repairs search projections before serving. Bundles here describe the objects they own and are
executed only through ``powercontext server db-migrate``.

The experimental fixture bundle is an isolated four-table prototype, not a migration for a
deployed PowerContext DB.
"""

from .bundle import MigrationBundle
from .models import MigrationError, MigrationPlan, MigrationResult
from .sqlite import SQLiteMigrationRunner

__all__ = ["MigrationBundle", "MigrationError", "MigrationPlan", "MigrationResult", "SQLiteMigrationRunner"]
