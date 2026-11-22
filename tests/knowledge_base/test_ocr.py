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

"""Recognize an actual image with the shipped RapidOCR ONNX models."""

import io
import sys
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "plugins" / "knowledge-base"))
from pc_knowledge_base.content import parse


@pytest.mark.parametrize(("mode", "background"), [("RGB", "white"), ("RGBA", (0, 0, 0, 0))])
def test_ocr_retains_text_and_image_provenance(mode, background):
    pytest.importorskip("rapidocr")
    image = Image.new(mode, (1000, 240), background)
    draw = ImageDraw.Draw(image)
    draw.text((30, 40), "Verify backups before restarting.", font=ImageFont.load_default(size=36), fill="black")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    blocks = parse(buffer.getvalue(), "checklist.png", {"max_chars": 360, "overlap": 40})
    assert "backups" in " ".join(b["text"].lower() for b in blocks)
    assert all(b["locator"]["polygon"] and b["locator"]["confidence"] > 0 for b in blocks)
    assert all((b["locator"]["image_width"], b["locator"]["image_height"]) == image.size for b in blocks)
