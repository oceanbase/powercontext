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

"""Pinned public cases and exact, bounded Experience evidence projections."""

from __future__ import annotations

import hashlib
import importlib
import json
import sys
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from time import perf_counter
from typing import Any

from powercontext.builtin.artifacts.generation import (
    ArtifactGenerationInput,
    GenerationEvidence,
    GenerationEvidenceKind,
)
from powercontext.builtin.inference.input_encoding import INPUT_ENCODING_SCHEMA_V2, decode_input, encode_input
from powercontext.builtin.sources.content import ContentSource
from powercontext.sources.models import SourceMaterialization

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "coding/swebench_pro/src"))
_catalog = importlib.import_module("powercontext_eval_swebench_pro.catalog")
PUBLIC_V2_SHA256 = _catalog.PUBLIC_V2_SHA256
STABILITY_V1_TASK_SET = _catalog.STABILITY_V1_TASK_SET
instance_ids_for_task_set = _catalog.instance_ids_for_task_set


SCHEMA = "powercontext.input-encoding-evaluation/v1"
PROJECTION = "swepro-history-experience-v1"
MODES = ("off", "shadow", "active")
DEFAULT_DATASET = Path("/data/swebench/cache/swebench-pro.git/helper_code/sweap_eval_full_v2.jsonl")


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def write_json(path: Path, value: object) -> None:
    """Replace a complete record atomically; interrupted attempts remain visible."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def sources(instance) -> list[dict[str, Any]]:
    """Split once without duplication; never synthesize successful test execution."""
    parts = (
        ("problem", instance.problem_statement),
        ("reference-patch", instance.patch),
        ("reference-tests", instance.test_patch),
    )
    identity = hashlib.sha256(instance.instance_id.encode()).hexdigest()[:16]
    result = []
    for kind, content in parts:
        for offset in range(0, len(content), 12_000):
            result.append({
                "source_id": f"swe-{identity}-{kind}-{offset // 12_000:02}",
                "content": content[offset : offset + 12_000],
                "metadata": {
                    "dataset": "swebench-pro-public-v2",
                    "instance_id": instance.instance_id,
                    "repository": instance.repo,
                    "kind": kind,
                    "part": offset // 12_000,
                    "historical_evidence": True,
                    "test_execution": "not_executed_by_this_evaluation",
                },
            })
    return result


def projection(instance) -> ArtifactGenerationInput:
    evidence = []
    for source in sources(instance):
        content = ContentSource(
            name=source["source_id"],
            materialization=SourceMaterialization.CAPTURED,
            content=source["content"],
            metadata=source["metadata"],
        ).model_dump_json()
        evidence.append(
            GenerationEvidence(
                evidence_id=f"source:content/{source['source_id']}",
                kind=GenerationEvidenceKind.SOURCE,
                content=content[:64_000],
                truncated=len(content) > 64_000,
            )
        )
    return ArtifactGenerationInput(evidence=tuple(evidence))


def load_catalog(path: Path) -> Any:
    return _catalog.SweBenchProCatalog.load(path)


def stability_ids(catalog: Any) -> tuple[str, ...]:
    return instance_ids_for_task_set(catalog.instance_ids, STABILITY_V1_TASK_SET)


def offline(dataset: Path, output: Path, *, iterations: int = 3) -> dict[str, Any]:
    """Exhaust all 731 cases, including rejected sizes, without DB/model calls."""
    catalog = load_catalog(dataset)
    rows: list[dict[str, Any]] = []
    for instance_id in catalog.instance_ids:
        instance = catalog.require(instance_id)
        row: dict[str, Any] = {
            "instance_id": instance_id,
            "repository": instance.repo,
            "source_count": len(sources(instance)),
        }
        try:
            value = projection(instance)
            original = value.model_dump_json()
            reports = {}
            for mode in MODES:
                times = []
                result = encode_input(original, stage="experience.generate", mode=mode, schema=INPUT_ENCODING_SCHEMA_V2)
                for _ in range(iterations):
                    start = perf_counter()
                    result = encode_input(
                        original, stage="experience.generate", mode=mode, schema=INPUT_ENCODING_SCHEMA_V2
                    )
                    times.append((perf_counter() - start) * 1000)
                restored = (
                    decode_input(result.prompt, stage="experience.generate", schema=INPUT_ENCODING_SCHEMA_V2)
                    if result.prompt != original
                    else original
                )
                reports[mode] = {
                    **asdict(result.report),
                    "roundtrip_equal": restored == original,
                    "latency_ms": times,
                }
            row.update({"status": "measured", "truncated": any(x.truncated for x in value.evidence), "modes": reports})
        except ValueError as error:
            row.update({"status": "projection_rejected", "error_type": type(error).__name__})
        rows.append(row)
    report = {
        "schema": SCHEMA,
        "projection": PROJECTION,
        "encoding_schema": INPUT_ENCODING_SCHEMA_V2,
        "dataset_sha256": PUBLIC_V2_SHA256,
        "codec_sha256": hashlib.sha256(
            Path(str(sys.modules[encode_input.__module__].__file__)).read_bytes()
        ).hexdigest(),
        "dataset_count": len(rows),
        "dataset_bytes": dataset.stat().st_size,
        "stability_ids": stability_ids(catalog),
        "status_counts": dict(Counter(row["status"] for row in rows)),
        "active_reason_counts": dict(Counter(row["modes"]["active"]["reason"] for row in rows if "modes" in row)),
        "measurement": "local codec only; character estimator, no provider usage or model quality",
        "cases": rows,
    }
    write_json(output, report)
    return {key: value for key, value in report.items() if key != "cases"}
