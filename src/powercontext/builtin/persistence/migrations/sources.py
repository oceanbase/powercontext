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

"""Choose an immutable source history using the pinned inventory and main intent."""

from collections.abc import Mapping
from typing import Any

from .bundle import MigrationBundle
from .models import MigrationError, digest
from .mysql_schema import MySQLSchema


class MigrationSources:
    def __init__(self, default: MigrationBundle, sources: tuple[MigrationBundle, ...]) -> None:
        self.default = default
        self.sources = sources
        revisions = [source.source_revision for source in sources]
        if None in revisions or len(set(revisions)) != len(revisions):
            raise MigrationError("invalid_bundle", "Source histories need distinct registered source revisions.")
        self.revisions = tuple(
            dict.fromkeys(revision for bundle in (default, *sources) for revision in bundle.revisions)
        )

    def from_evidence(self, evidence: Mapping[str, Any] | None) -> MigrationBundle:
        if evidence is None:
            return self.default
        # source_revision identifies this maintenance window's recovery point.
        # Later windows start at a shared revision; the completed release history
        # still identifies the original source chain. Selection does not validate
        # those checksums: the runner must check every recorded revision.
        recorded = evidence.get("release_checksums", {})
        if not isinstance(recorded, Mapping):
            raise MigrationError("recovery_required", "The recorded migration history is invalid.")
        matches = [
            source
            for source in self.sources
            if source.source_revision == evidence.get("source_revision") or source.source_revision in recorded
        ]
        if len(matches) > 1:
            raise MigrationError("recovery_required", "The recorded history selects multiple source chains.")
        return matches[0] if matches else self.default

    def select(
        self,
        *,
        evidence: Mapping[str, Any] | None,
        revision: str | None,
        initialized: bool,
        inventory: list[list[Any]] | dict[str, Any],
        embedded_code: bool = False,
    ) -> MigrationBundle:
        if evidence is not None:
            return self.from_evidence(evidence)
        for source in self.sources:
            if initialized and revision == source.source_revision:
                # Selection does not authorize recovery without its original journal.
                return source
        if initialized or not self.sources:
            return self.default
        matches = [
            bundle
            for bundle in (self.default, *self.sources)
            if self._matches(bundle, inventory, embedded_code=embedded_code)
        ]
        if len(matches) > 1:
            raise MigrationError("invalid_bundle", "The source layout matches multiple registered histories.")
        return matches[0] if matches else self.default

    @staticmethod
    def _matches(bundle, inventory, *, embedded_code: bool) -> bool:
        if isinstance(inventory, list):
            excluded = {"pc_schema_revision", *bundle.retained_objects}
            fingerprint = digest([row for row in inventory if row[1] not in excluded and row[2] not in excluded])
            return fingerprint in bundle.baselines
        schema = MySQLSchema(bundle, embedded_code=embedded_code)
        try:
            return schema.identify(inventory) in bundle.baselines.values()
        except MigrationError as error:
            if error.code != "incompatible_schema":
                raise
            return False
