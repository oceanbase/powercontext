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

"""Immutable originals and pinned processing requests; only requests are consumed."""

from pc_knowledge_base.models import Evidence
from powercontext.sources import Source, SourceMaterialization


class FileEvidence(Evidence):
    pass


class ProcessEvidence(Evidence):
    pass


class FileSource(Source):
    payload: dict[str, object]


class ProcessSource(Source):
    payload: dict[str, object]


class Projection:
    name = "knowledge-base.input"
    version = "1"

    def __init__(self, source_class, output_class):
        self.source_class = source_class
        self.output_class = output_class

    def project(self, source, /):
        return self.output_class(key=source.name, payload=source.payload)


class Definition:
    version = "1"

    def __init__(self, name):
        self.name = name
        self.input_class = FileEvidence if name == "knowledge-base-file" else ProcessEvidence
        self.source_class = FileSource if name == "knowledge-base-file" else ProcessSource
        self.projections = (Projection(self.source_class, self.input_class),)

    async def resolve(self, value, /):
        return self.source_class(
            name=value.key,
            materialization=SourceMaterialization.CAPTURED,
            description="Knowledge-base immutable evidence",
            payload=value.payload,
        )

    async def read(self, source, /):
        return self.projections[0].project(source)
