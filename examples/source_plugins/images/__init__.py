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

"""Server-local PNG/JPEG write capabilities; install powercontext[source-images]."""

from powercontext.source_management import Capability

from .definition import ImageCapture, ImageDefinition

image_definition = ImageDefinition()
definitions = (image_definition,)
capabilities = (
    Capability(
        name="images.write",
        version="1",
        kind="write",
        description="Save a supplied PNG or JPEG image",
        input_schema=ImageCapture.model_json_schema(),
        definition_name=image_definition.name,
        definition_version=image_definition.version,
    ),
)
