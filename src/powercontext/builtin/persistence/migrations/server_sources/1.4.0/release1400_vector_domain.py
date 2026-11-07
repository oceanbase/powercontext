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

"""Join the shared vector profile without converting already-current Candidates."""

import runpy
from pathlib import Path

root = Path(__file__).parent
shared = runpy.run_path(str(root / "server_vector_domain.py"))
source = runpy.run_path(str(root / "release1400_domain.py"))


def preflight(connection, *, configuration):
    shared["preflight"](connection, configuration=configuration)
    source["validate_source_data"](connection, configuration=configuration)


def apply(connection, *, configuration):
    shared["apply"](connection, configuration=configuration)


def verify(connection, *, configuration):
    validate(connection, configuration=configuration)


def validate(connection, *, configuration):
    shared["validate"](connection, configuration=configuration)
