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

"""Deterministic source files and independently specified expected outputs."""

from __future__ import annotations

import hashlib
import io
import json
import zlib
from base64 import b64decode
from pathlib import Path
from typing import NotRequired, TypedDict
from xml.sax.saxutils import escape
from zipfile import ZIP_STORED, ZipFile, ZipInfo

RASTER_ASSETS = Path(__file__).with_name("raster_assets.json")
OFFICE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PACKAGE = "http://schemas.openxmlformats.org/package/2006"
SCHEMAS = "http://schemas.openxmlformats.org"
MIME = "application/vnd.openxmlformats-officedocument"
IMAGE_TEXT = {
    "meter.jpg": "Inspect the pressure gauge",
    "vault.png": "Rotate the backup token",
    "radio.jpeg": "Calibrate the receiver",
    "filter.webp": "Clean the intake filter",
}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_text_bytes(path: Path) -> bytes:
    return path.read_text(encoding="utf-8").encode("utf-8")


def json_bytes(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()


def relationships(rows: list[tuple[str, str]]) -> str:
    elements = "".join(
        f'<Relationship Id="rId{i}" Type="{OFFICE}/{kind}" Target="{path}"/>' for i, (kind, path) in enumerate(rows, 1)
    )
    return f'<Relationships xmlns="{PACKAGE}/relationships">{elements}</Relationships>'


def office(parts: dict[str, tuple[str, str]], main: str) -> bytes:
    entries = {
        "[Content_Types].xml": (
            f'<Types xmlns="{PACKAGE}/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            + "".join(
                f'<Override PartName="/{name}" ContentType="{MIME}.{kind}+xml"/>'
                for name, (kind, _) in parts.items()
                if kind
            )
            + "</Types>"
        ),
        "_rels/.rels": relationships([("officeDocument", main)]),
    }
    entries.update({name: body for name, (_, body) in parts.items()})
    buffer = io.BytesIO()
    with ZipFile(buffer, "w", compression=ZIP_STORED) as archive:
        for name, body in sorted(entries.items()):
            member = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            member.create_system = 3
            member.external_attr = 0o644 << 16
            archive.writestr(member, body)
    return buffer.getvalue()


def docx() -> bytes:
    def paragraph(text: str) -> str:
        return f"<w:p><w:r><w:t>{escape(text)}</w:t></w:r></w:p>"

    body = (
        paragraph("Replace the valve seal")
        + '<w:tbl><w:tblPr/><w:tblGrid><w:gridCol w:w="1800"/></w:tblGrid>'
        + "<w:tr><w:tc>"
        + paragraph("Gasket")
        + "</w:tc></w:tr></w:tbl>"
        + '<w:sectPr><w:pgSz w:w="12240" w:h="15840"/></w:sectPr>'
    )
    name = "word/document.xml"
    return office(
        {
            name: (
                "wordprocessingml.document.main",
                f'<w:document xmlns:w="{SCHEMAS}/wordprocessingml/2006/main"><w:body>{body}</w:body></w:document>',
            )
        },
        name,
    )


def xlsx() -> bytes:
    ns = f'xmlns="{SCHEMAS}/spreadsheetml/2006/main"'
    name = "xl/workbook.xml"
    parts = {
        name: (
            "spreadsheetml.sheet.main",
            f'<workbook {ns} xmlns:r="{OFFICE}"><sheets>'
            '<sheet name="Stock" sheetId="1" r:id="rId1"/></sheets></workbook>',
        ),
        "xl/_rels/workbook.xml.rels": ("", relationships([("worksheet", "worksheets/sheet1.xml")])),
        "xl/worksheets/sheet1.xml": (
            "spreadsheetml.worksheet",
            f'<worksheet {ns}><sheetData><row r="1"><c r="A1" t="inlineStr">'
            '<is><t>Copper inventory</t></is></c><c r="B1"><v>17</v></c></row></sheetData></worksheet>',
        ),
    }
    return office(parts, name)


def pptx() -> bytes:
    ns = f'xmlns:p="{SCHEMAS}/presentationml/2006/main" xmlns:a="{SCHEMAS}/drawingml/2006/main"'
    group = '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr/>'
    shape = (
        '<p:sp><p:nvSpPr><p:cNvPr id="2" name="Text"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr>'
        '<p:spPr><a:xfrm><a:off x="914400" y="914400"/><a:ext cx="3657600" cy="914400"/></a:xfrm>'
        "</p:spPr><p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:t>Wind turbine inspection</a:t>"
        "</a:r></a:p></p:txBody></p:sp>"
    )
    name = "ppt/presentation.xml"
    return office(
        {
            name: (
                "presentationml.presentation.main",
                f'<p:presentation {ns} xmlns:r="{OFFICE}"><p:sldIdLst><p:sldId id="256" r:id="rId1"/>'
                '</p:sldIdLst><p:sldSz cx="9144000" cy="6858000"/><p:notesSz cx="6858000" cy="9144000"/>'
                "</p:presentation>",
            ),
            "ppt/_rels/presentation.xml.rels": ("", relationships([("slide", "slides/slide1.xml")])),
            "ppt/slides/slide1.xml": (
                "presentationml.slide",
                f'<p:sld {ns} show="0"><p:cSld><p:spTree>{group}{shape}</p:spTree></p:cSld></p:sld>',
            ),
            "ppt/slides/_rels/slide1.xml.rels": (
                "",
                relationships([("slideLayout", "../slideLayouts/slideLayout1.xml")]),
            ),
            "ppt/slideLayouts/slideLayout1.xml": (
                "presentationml.slideLayout",
                f'<p:sldLayout {ns} type="blank"><p:cSld><p:spTree>{group}</p:spTree></p:cSld></p:sldLayout>',
            ),
        },
        name,
    )


def pdf(raster: bytes) -> bytes:
    def stream(data: bytes, metadata: bytes = b"") -> bytes:
        return b"<< /Length %d %s >>\nstream\n" % (len(data), metadata) + data + b"\nendstream"

    page1 = b"BT /F1 20 Tf 45 95 Td (Emergency drainage) Tj ET"
    page2 = b"q 250 0 0 42 20 65 cm /Scan Do Q"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Count 2 /Kids [3 0 R 4 0 R] >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 320 180] /CropBox [20 30 300 160] /Rotate 90 "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 6 0 R >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 320 180] /Resources << /XObject << /Scan 8 0 R >> >> "
        b"/Contents 7 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        stream(page1),
        stream(page2),
        stream(
            raster,
            b"/Type /XObject /Subtype /Image /Width 720 /Height 120 /ColorSpace /DeviceGray /BitsPerComponent 8",
        ),
    ]
    data = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for number, item in enumerate(objects, 1):
        offsets.append(len(data))
        data.extend(b"%d 0 obj\n" % number + item + b"\nendobj\n")
    xref = len(data)
    data.extend(b"xref\n0 %d\n0000000000 65535 f \n" % (len(offsets) + 1))
    for offset in offsets:
        data.extend(b"%010d 00000 n \n" % offset)
    data.extend(b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(offsets) + 1, xref))
    return bytes(data)


def span(start: int, end: int) -> dict[str, int]:
    return {"start": start, "end": end}


class ExpectedBlock(TypedDict):
    text: str
    locator: dict[str, object]
    region_center: NotRequired[list[list[float]]]


class ExpectedFixture(TypedDict):
    coverage: dict[str, object]
    blocks: list[ExpectedBlock]
    query: str


def label(text: str, kind: str, **location: object) -> ExpectedBlock:
    return {"text": text, "locator": {"kind": kind, **location}}


def expected(unit: str, blocks: list[ExpectedBlock], query: str, total: int | None = None) -> ExpectedFixture:
    return {
        "coverage": {
            "unit": unit,
            "covered": len(blocks) if total is None else total,
            "total": len(blocks) if total is None else total,
            "warnings": [],
        },
        "blocks": blocks,
        "query": query,
    }


def generate() -> tuple[dict[str, bytes], dict[str, ExpectedFixture]]:
    raster_assets = json.loads(RASTER_ASSETS.read_bytes())["base64"]
    text = "检查😀冷却泵。\n"
    files = {
        "service.md": b"# Maintenance\n\nCheck fan bearings.\n",
        "cooling.txt": ("\ufeff" + text).encode(),
        "drainage.pdf": pdf(zlib.decompress(b64decode(raster_assets["drainage-page2.gray8.zlib"], validate=True))),
        "seal.docx": docx(),
        "stock.xlsx": xlsx(),
        "wind.pptx": pptx(),
        "shift.csv": b'shift,task\r\n2,"Stow tools, close bay"\r\n',
    }
    labels = {
        "service.md": expected(
            "block",
            [
                label("# Maintenance", "lines", lines=span(0, 1)),
                label("Check fan bearings.", "lines", lines=span(2, 3)),
            ],
            "Which part of the fan should be checked?",
        ),
        "cooling.txt": expected(
            "block",
            [
                label(text.rstrip(), "characters", characters=span(0, len(text.rstrip()))),
            ],
            "应该检查哪台冷却设备?",
        ),
        "drainage.pdf": expected(
            "page",
            [
                label("Emergency drainage", "page", page=0),
                label("Close the drainage gate", "page", page=1),
            ],
            "What action is required at the drainage gate?",
            total=2,
        ),
        "seal.docx": expected(
            "block",
            [
                label("Replace the valve seal", "paragraph", paragraph=0),
                label("Gasket", "table", table=0, rows=span(0, 1), columns=span(0, 1)),
            ],
            "What should be replaced on the valve?",
        ),
        "stock.xlsx": expected(
            "sheet",
            [
                label("Copper inventory", "sheet", sheet=0, rows=span(0, 1), columns=span(0, 1)),
                label("17", "sheet", sheet=0, rows=span(0, 1), columns=span(1, 2)),
            ],
            "Where is the copper stock listed?",
            total=1,
        ),
        "wind.pptx": expected(
            "slide",
            [
                label("Wind turbine inspection", "slide", slide=0, shape_path=[0]),
            ],
            "Which turbine is scheduled for inspection?",
        ),
        "shift.csv": expected(
            "record",
            [
                label("shift", "records", rows=span(0, 1), columns=span(0, 1)),
                label("task", "records", rows=span(0, 1), columns=span(1, 2)),
                label("2", "records", rows=span(1, 2), columns=span(0, 1)),
                label("Stow tools, close bay", "records", rows=span(1, 2), columns=span(1, 2)),
            ],
            "What should the second shift do before leaving?",
            total=2,
        ),
    }
    image_queries = (
        "How do I check the pressure instrument?",
        "What do I rotate to secure backups?",
        "What radio component needs calibration?",
        "Which filter should be cleaned?",
    )
    for (name, words), query in zip(
        IMAGE_TEXT.items(),
        image_queries,
        strict=True,
    ):
        files[name] = b64decode(raster_assets[name], validate=True)
        labels[name] = expected("block", [label(words, "image", index=0)], query)
        labels[name]["coverage"].update(
            total=None, warnings=[{"code": "coverage_unknown", "feature": "unrecognized_image_content"}]
        )
    # Normalized centers with independently chosen bounds around the drawn text.
    labels["drainage.pdf"]["blocks"][0]["region_center"] = [[0.49, 0.70], [0.30, 0.48]]
    labels["drainage.pdf"]["blocks"][1]["region_center"] = [[0.22, 0.46], [0.45, 0.59]]
    for name in IMAGE_TEXT:
        labels[name]["blocks"][0]["region_center"] = [[0.18, 0.43], [0.33, 0.56]]
    return files, labels


def manifest(files: dict[str, bytes], labels: dict[str, ExpectedFixture]) -> dict[str, object]:
    digests = {name: sha256(data) for name, data in files.items()}
    return {
        "schema_version": 1,
        "license": "Apache-2.0",
        "generator_sha256": sha256(canonical_text_bytes(Path(__file__))),
        "raster_assets_sha256": sha256(canonical_text_bytes(RASTER_ASSETS)),
        "raster_toolchain": json.loads(RASTER_ASSETS.read_bytes())["toolchain"],
        "dataset_sha256": sha256(json_bytes(digests)),
        "expected_sha256": sha256(json_bytes(labels)),
        "files": digests,
        "expected": labels,
    }
