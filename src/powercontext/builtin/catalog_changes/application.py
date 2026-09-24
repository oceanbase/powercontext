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

"""Scope-bound public catalog review facade."""

from collections.abc import Callable

from powercontext.builtin.catalog_changes.service import CatalogChangeService
from powercontext.builtin.sources import validate_scope_id


class CatalogChangeApplication:
    def __init__(self, service_factory: Callable[[str], CatalogChangeService]) -> None:
        self._service_factory = service_factory

    def for_scope(self, scope_id: str) -> CatalogChangeService:
        return self._service_factory(validate_scope_id(scope_id))
