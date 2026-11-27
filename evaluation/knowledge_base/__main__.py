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

"""Verify fixture hashes and score parser and retrieval output separately."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import platform
import sys
import unicodedata
from difflib import SequenceMatcher
from importlib.metadata import distribution, version
from pathlib import Path
from time import perf_counter
from typing import TypedDict, cast

from evaluation.knowledge_base.fixtures import (
    ExpectedFixture,
    canonical_text_bytes,
    generate,
    json_bytes,
    manifest,
    sha256,
)
from evaluation.knowledge_base.legacy import RECORD

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "plugins" / "knowledge-base"))
from pc_knowledge_base.content import CAPABILITIES, OCR_ASSETS, parse_document, parser_profile  # noqa: E402
from pc_knowledge_base.embedding import Embedding  # noqa: E402
from pc_knowledge_base.parser_contract import ParsedDocument, context_locator  # noqa: E402

THRESHOLDS = {
    "min_text_similarity": 1.0,
    "ordered_locations": 1.0,
    "coverage": 1.0,
}
PROVISIONAL_RETRIEVAL = {
    "status": "PROVISIONAL_UNTIL_2856_MEASUREMENT",
    "recall_at_3": 0.90,
    "mrr_at_3": 0.70,
    "basis": "Proposed policy for 11 independently labeled queries, allowing at most one top-three miss and requiring at least 7.7 reciprocal-rank points. No observed embedding scores informed these values.",
}
REPLAY_TOOLCHAIN = {"implementation": "CPython", "python": "3.12.14", "unicode": "15.0.0"}


class EmbeddingBaseline(TypedDict):
    model: str
    repository: str
    revision: str
    fastembed: str
    onnxruntime: str
    files: dict[str, str]


EMBEDDING: EmbeddingBaseline = {
    "model": "BAAI/bge-small-zh-v1.5",
    "repository": "Qdrant/bge-small-zh-v1.5",
    "revision": "46fbe35fd4374a00fee7de77dfddaeb6dd6a2c59",
    "fastembed": "0.9.1",
    "onnxruntime": "1.32.0",
    "files": {
        "config.json": "9088751d39abbf86ec3d19ffca92ad62ad19075f7e59712e6c71217fa125d1d3",
        "model_optimized.onnx": "1294ea4b6331115a353d81f96b85e8c8d7fdcc284453d5b2fab5b016230aad38",
        "special_tokens_map.json": "b6d346be366a7d1d48332dbc9fdf3bf8960b5d879522b7799ddba59e76237ee3",
        "tokenizer.json": "48cea5d44424912a6fd1ea647bf4fe50b55ab8b1e5879c3275f80e339e8fae26",
        "tokenizer_config.json": "e6f3b96db926a37d4039995fbf5ad17de158dfb8f6343d607e4dbaad18d75f5a",
    },
}
PARSER_PACKAGES = {
    "markdown-it-py": "4.2.0",
    "rapidocr": "3.10.0",
    "pillow": "12.3.0",
}
FORMAT_PACKAGES = {"defusedxml": "0.7.1", "pypdfium2": "5.15.2"}
FUTURE_PARSERS = {
    "txt/csv": "stdlib Python 3.12.14",
    "pdf": "NOT_IMPLEMENTED; proposed pypdfium2 5.15.2 / PDFium 159.0.8149.0",
    "docx/xlsx/pptx": "NOT_IMPLEMENTED; proposed bounded stdlib ZIP + defusedxml 0.7.1",
}


def baseline_manifest(files, expected):
    return {
        **manifest(files, expected),
        "thresholds": THRESHOLDS,
        "parsing_threshold_basis": {
            "min_text_similarity": "Exact normalized text in independent annotations for the legacy fixtures.",
            "ordered_locations": "Exact ordered match to independently authored source anchors.",
            "coverage": "Exact counts and warnings from authored extraction units and OCR unknown-total semantics.",
        },
        "provisional_retrieval": PROVISIONAL_RETRIEVAL,
        "embedding": EMBEDDING,
        "parser_packages": PARSER_PACKAGES,
        "parser_profiles": {
            extension: parser_profile(f"input{extension}").model_dump(mode="json") for extension in CAPABILITIES
        },
        "format_packages": FORMAT_PACKAGES,
        "future_parser_stacks": FUTURE_PARSERS,
        "ocr_assets": OCR_ASSETS,
        "replay_toolchain": REPLAY_TOOLCHAIN,
        "legacy_baseline_sha256": sha256(canonical_text_bytes(RECORD)),
        "scorer_sha256": sha256(canonical_text_bytes(Path(__file__))),
    }


def verify():
    if (
        platform.python_implementation() != REPLAY_TOOLCHAIN["implementation"]
        or platform.python_version() != REPLAY_TOOLCHAIN["python"]
        or unicodedata.unidata_version != REPLAY_TOOLCHAIN["unicode"]
    ):
        raise SystemExit(f"Expected {REPLAY_TOOLCHAIN}, got {platform.python_version()} on {platform.platform()}")  # noqa: TRY003
    for name, expected_version in {
        **PARSER_PACKAGES,
        **FORMAT_PACKAGES,
        "fastembed": EMBEDDING["fastembed"],
        "onnxruntime": EMBEDDING["onnxruntime"],
    }.items():
        if version(name) != expected_version:
            raise SystemExit(f"Expected {name} {expected_version}")  # noqa: TRY003
    rapidocr = distribution("rapidocr")
    for name, expected_hash in OCR_ASSETS.items():
        path = Path(str(rapidocr.locate_file(f"rapidocr/{name}")))
        if not path.is_file():
            raise SystemExit(f"Missing locked RapidOCR artifact: {name}")  # noqa: TRY003
        with path.open("rb") as file:
            if hashlib.file_digest(file, "sha256").hexdigest() != expected_hash:
                raise SystemExit(f"Unexpected RapidOCR artifact hash: {name}")  # noqa: TRY003
    files, expected = generate()
    observed = baseline_manifest(files, expected)
    recorded = canonical_text_bytes(HERE / "manifest.json")
    recorded_profiles = json.loads(recorded).get("parser_profiles", {})
    for extension, profile in observed["parser_profiles"].items():
        if recorded_profiles.get(extension) != profile:
            raise SystemExit(f"Installed parser profile for {extension} differs from manifest.json")  # noqa: TRY003
    if json_bytes(observed) != recorded:
        raise SystemExit("Fixture, labels, thresholds, version or evaluator hash differs from manifest.json")  # noqa: TRY003
    return files, expected


def text_similarity(expected: str, actual: str) -> float:
    def normalize(text):
        return " ".join(unicodedata.normalize("NFC", text).split())

    return SequenceMatcher(None, normalize(expected), normalize(actual), autojunk=False).ratio()


def matches_locator(
    expected: dict[str, object], location: dict[str, object], region_center: list[list[float]] | None = None
) -> bool:
    if any(location.get(key) != value for key, value in expected.items()):
        return False
    if region_center is None:
        return True
    polygon = cast("list[list[float]] | None", location.get("polygon"))
    if not polygon:
        return False
    return all(
        lo <= sum(axis) / len(axis) <= hi
        for axis, (lo, hi) in zip(zip(*polygon, strict=True), region_center, strict=True)
    )


def score_parse(results: dict[str, ParsedDocument], expected: dict[str, ExpectedFixture]) -> bool:
    passed = True
    for name, parsed in results.items():
        target = expected[name]
        blocks = parsed.blocks
        expected_blocks = target["blocks"]
        ordered = len(blocks) == len(expected_blocks) and all(
            matches_locator(label["locator"], context_locator(block.locator), label.get("region_center"))
            for label, block in zip(expected_blocks, blocks, strict=True)
        )
        similarity = text_similarity(
            "\n".join(block["text"] for block in expected_blocks),
            "\n".join(block.text for block in blocks),
        )
        coverage = parsed.coverage.model_dump(include={"unit", "covered", "total"})
        coverage["warnings"] = [
            {
                "code": warning.code,
                "feature": warning.feature,
                **({"locator": context_locator(warning.locator)} if warning.locator is not None else {}),
            }
            for warning in parsed.coverage.warnings
        ]
        coverage_matches = coverage == target["coverage"]
        ok = (
            float(ordered) >= THRESHOLDS["ordered_locations"]
            and similarity >= THRESHOLDS["min_text_similarity"]
            and float(coverage_matches) >= THRESHOLDS["coverage"]
        )
        passed &= ok
        print(f"{name}: text={similarity:.3f} locations={int(ordered)} coverage={int(coverage_matches)}")
    return passed


def retrieval_metrics(ranks: list[int]) -> tuple[float, float]:
    reciprocal_ranks = [1 / rank if 1 <= rank <= 3 else 0 for rank in ranks]
    return sum(rank > 0 for rank in reciprocal_ranks) / len(ranks), math.fsum(reciprocal_ranks) / len(ranks)


async def score_retrieval(
    results: dict[str, ParsedDocument], expected: dict[str, ExpectedFixture], cache: Path
) -> None:
    embed = Embedding(EMBEDDING["model"], str(cache))
    model_dir = cache / "models--Qdrant--bge-small-zh-v1.5" / "snapshots" / EMBEDDING["revision"]
    for name, digest in EMBEDDING["files"].items():
        path = model_dir / name
        if not path.is_file() or sha256(path.read_bytes()) != digest:
            raise SystemExit(f"Missing or incorrect offline model artifact: {name}")  # noqa: TRY003
    owners = [name for name, parsed in results.items() for block in parsed.blocks if block.text.strip()]
    text = [block.text for parsed in results.values() for block in parsed.blocks if block.text.strip()]
    names = sorted(results)
    passages = await embed.embed(text)
    queries = await embed.embed([expected[name]["query"] for name in names], query=True)
    ranks = []
    for target, query in zip(names, queries, strict=True):
        scores = dict.fromkeys(names, -math.inf)
        for owner, passage in zip(owners, passages, strict=True):
            score = math.fsum(x * y for x, y in zip(query, passage, strict=True)) / (
                math.hypot(*query) * math.hypot(*passage)
            )
            scores[owner] = max(scores[owner], score)
        ranked = sorted((name for name in names if scores[name] > -math.inf), key=lambda name: (-scores[name], name))
        rank = ranked.index(target) + 1 if target in ranked else 0
        ranks.append(rank)
        print(f"{target}: rank={rank}")
    recall, mrr = retrieval_metrics(ranks)
    print(f"recall@3={recall:.3f} mrr@3={mrr:.3f} acceptance={PROVISIONAL_RETRIEVAL['status']}")


def verify_metrics() -> None:
    anchor: dict[str, object] = {"kind": "page", "page": 0}
    center = [[0.4, 0.6], [0.4, 0.6]]
    location: dict[str, object] = {**anchor, "polygon": [[0.4, 0.4], [0.6, 0.6], [0.5, 0.5]]}
    examples = (
        ("Unicode text", text_similarity("Cafe\u0301  note", "Café note"), 1),
        ("Missing text", text_similarity("ABC", "AC"), 0.8),
        ("Known geometry", matches_locator(anchor, location, center), True),
        ("Wrong page", matches_locator(anchor, {**location, "page": 1}, center), False),
        ("Wrong position", matches_locator(anchor, {**location, "polygon": [[0, 0]] * 3}, center), False),
        ("Mixed ranks", retrieval_metrics([1, 2, 3, 4]), (0.75, 11 / 24)),
        ("All missed", retrieval_metrics([4, 0]), (0.0, 0.0)),
    )
    for name, actual, expected in examples:
        if actual != expected:
            raise SystemExit(f"Metric example failed for {name}: {actual} != {expected}")  # noqa: TRY003


def dry_run(files: dict[str, bytes]) -> None:
    verify_metrics()
    print(f"Verified {len(files)} fixtures, expected outputs, toolchain, model configuration and metric examples")
    for name in files:
        if Path(name).suffix not in CAPABILITIES:
            print(f"{name}: NOT_IMPLEMENTED")
    print(f"Fixed-model retrieval: NOT_RUN ({PROVISIONAL_RETRIEVAL['status']})")


def read_results(directory: Path, files: dict[str, bytes], *, subset: bool) -> dict[str, ParsedDocument]:
    documents = {}
    for name, source in files.items():
        result_path = directory / f"{name}.json"
        if subset and not result_path.is_file():
            continue
        document = ParsedDocument.model_validate_json(result_path.read_bytes())
        if document.source_digest != sha256(source):
            raise SystemExit(f"Source digest mismatch for {name}")  # noqa: TRY003
        extension = Path(name).suffix
        if extension in CAPABILITIES:
            profile = parser_profile(name)
            if (
                document.format != CAPABILITIES[extension].format
                or document.profile != profile
                or profile.version != PARSER_PACKAGES[profile.parser]
            ):
                raise SystemExit(f"Parser profile or format differs from the baseline for {name}")  # noqa: TRY003
        else:
            future_formats = {
                ".txt": "text",
                ".pdf": "pdf",
                ".csv": "csv",
                ".docx": "docx",
                ".xlsx": "xlsx",
                ".pptx": "pptx",
            }
            if document.format != future_formats[extension]:
                raise SystemExit(f"Format differs from the baseline for {name}")  # noqa: TRY003
        print(f"{name}: profile={document.profile.model_dump_json()}")
        documents[name] = document
    if not documents:
        raise SystemExit("No parser results found")  # noqa: TRY003
    return documents


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("dry-run")
    for action in ("generate", "export-existing"):
        commands.add_parser(action).add_argument("directory", type=Path)
    parse = commands.add_parser("parse")
    parse.add_argument("results", type=Path)
    parse.add_argument("--subset", action="store_true", help="Report partial evidence")
    retrieval = commands.add_parser("retrieve")
    retrieval.add_argument("results", type=Path)
    retrieval.add_argument("--model-cache", required=True, type=Path)
    args = parser.parse_args()
    files, expected = verify()
    if args.command == "dry-run":
        dry_run(files)
    elif args.command == "generate":
        args.directory.mkdir(parents=True, exist_ok=True)
        for name, data in files.items():
            (args.directory / name).write_bytes(data)
    elif args.command == "export-existing":
        args.directory.mkdir(parents=True, exist_ok=True)
        for name, data in files.items():
            if Path(name).suffix in CAPABILITIES:
                start = perf_counter()
                output = parse_document(data, name)
                (args.directory / f"{name}.json").write_text(output.model_dump_json(), encoding="utf-8")
                print(f"{name}: {perf_counter() - start:.3f} s profile={output.profile.model_dump_json()}")
    elif args.command == "parse":
        results = read_results(args.results, files, subset=args.subset)
        print(f"Parser results: {len(results)}/{len(files)}")
        return int(not score_parse(results, expected))
    else:
        results = read_results(args.results, files, subset=False)
        asyncio.run(score_retrieval(results, expected, args.model_cache))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
