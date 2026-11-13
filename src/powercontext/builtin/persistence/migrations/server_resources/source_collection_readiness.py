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

"""Server readiness for Source collection runners and targets at v1.5.0.006.

This revision adds the runner and target tables and drops the registry of the
removed Source write plugin framework. Collection bindings are named
"plugin:<name>" like other plugin bindings, so the v1.5.0.005 readiness contract
applies unchanged.
"""

import runpy
from pathlib import Path

from powercontext.builtin.persistence.migrations.models import MigrationError

previous = runpy.run_path(str(Path(__file__).parent / "plugin_artifact_readiness.py"))


def preflight(connection, *, configuration):
    """Refuse to drop the write plugin registry while it still lists plugins."""
    from sqlalchemy import inspect

    if (
        "pc_source_write_plugins" in inspect(connection).get_table_names()
        and connection.exec_driver_sql("SELECT 1 FROM pc_source_write_plugins LIMIT 1").first()
    ):
        raise MigrationError(
            "unsupported_plugin_data",
            "Source write plugins of the removed framework are still loaded; unload them on the previous version"
            " and provide their Source types as Source plugins before upgrading.",
        )


def apply(connection, *, configuration):
    """The schema transition drops only an empty registry and adds empty tables; no records are rewritten."""


def verify(connection, *, configuration):
    validate(connection, configuration=configuration)


def validate(connection, *, configuration):
    previous["validate"](connection, configuration=configuration)
