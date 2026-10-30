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

"""Captured PNG/JPEG content with validated bytes and immutable identity."""

from __future__ import annotations

import base64
import binascii
import hashlib
import io

from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field, model_validator

from powercontext.sources import Source, SourceMaterialization


class ImageCapture(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    media_type: str
    data_base64: str = Field(min_length=1, max_length=1024 * 1024)
    title: str = Field(default="", max_length=256)

    @model_validator(mode="after")
    def validate_image(self) -> ImageCapture:
        try:
            raw = base64.b64decode(self.data_base64, validate=True)
            if base64.b64encode(raw).decode() != self.data_base64:
                raise ValueError("Use canonical Base64")  # noqa: TRY003
            with Image.open(io.BytesIO(raw)) as image:
                if image.format not in {"PNG", "JPEG"} or self.media_type != Image.MIME[image.format]:
                    raise ValueError("Supply a PNG or JPEG matching media_type")  # noqa: TRY003
                if image.width * image.height > 16_000_000:
                    raise ValueError("Image exceeds 16 million pixels")  # noqa: TRY003
                image.verify()
            with Image.open(io.BytesIO(raw)) as image:
                image.load()
        except (binascii.Error, OSError, UnidentifiedImageError, Image.DecompressionBombError) as error:
            raise ValueError("Invalid image encoding or content") from error  # noqa: TRY003
        return self

    def snapshot_id(self) -> str:
        return "image-" + hashlib.sha256(self.model_dump_json().encode()).hexdigest()


class ImageSource(Source):
    capture: ImageCapture


class ImageDefinition:
    name = "example-image"
    version = "1"
    input_class = ImageCapture
    source_class = ImageSource
    projections = ()

    async def resolve(self, value: ImageCapture, /) -> ImageSource:
        return ImageSource(
            name=value.snapshot_id(),
            materialization=SourceMaterialization.CAPTURED,
            description=value.title or "Captured image",
            capture=value,
        )

    async def read(self, source: ImageSource, /) -> ImageCapture:
        return source.capture
