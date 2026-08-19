"""Immutable catalog for the pinned public SWE-bench Pro v2 task set."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Self

from powercontext_eval.benchmarks.swebench_pro.adapter import DatasetSchemaError, SweBenchProInstance
from powercontext_eval.errors import PowerContextEvalError

PUBLIC_V2_COUNT = 731
PUBLIC_V2_SHA256 = "b5b2462bfbf5aeb2cb7ba7d215778a1768b85f9d7ad7f748546c7f80a0ad1510"
PUBLIC_V2_TASK_SET = "swebench-pro-public-v2"


class CatalogError(PowerContextEvalError):
    """The pinned task-set file cannot be trusted or indexed."""


@dataclass(frozen=True)
class SweBenchProCatalog:
    """An in-memory, source-ordered index of a verified dataset file."""

    dataset_path: Path
    dataset_sha256: str
    instances: Mapping[str, SweBenchProInstance]
    instance_ids: tuple[str, ...]

    @classmethod
    def load(
        cls,
        path: Path,
        *,
        expected_sha256: str = PUBLIC_V2_SHA256,
        expected_count: int = PUBLIC_V2_COUNT,
    ) -> Self:
        try:
            payload = path.read_bytes()
        except OSError as error:
            raise CatalogError(f"Cannot read SWE-bench Pro dataset: {path}") from error
        digest = hashlib.sha256(payload).hexdigest()
        if digest != expected_sha256:
            raise CatalogError(f"Dataset SHA-256 mismatch: expected {expected_sha256}, got {digest}")
        if not payload.strip():
            raise CatalogError("SWE-bench Pro dataset is blank")
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError as error:
            raise CatalogError("SWE-bench Pro dataset is not valid UTF-8") from error

        instances: dict[str, SweBenchProInstance] = {}
        instance_ids: list[str] = []
        for line_number, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                raise CatalogError(f"Dataset contains a blank row at line {line_number}")
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as error:
                raise CatalogError(f"Dataset line {line_number} is not valid JSON") from error
            if not isinstance(raw, dict):
                raise CatalogError(f"Dataset line {line_number} must be a JSON object")
            try:
                instance = SweBenchProInstance.from_public_raw(raw)
            except DatasetSchemaError as error:
                raise CatalogError(f"Dataset line {line_number}: {error}") from error
            if instance.instance_id in instances:
                raise CatalogError(f"Dataset contains duplicate instance_id: {instance.instance_id}")
            instances[instance.instance_id] = instance
            instance_ids.append(instance.instance_id)

        if len(instance_ids) != expected_count:
            raise CatalogError(f"Dataset count mismatch: expected {expected_count}, got {len(instance_ids)}")
        return cls(
            dataset_path=path,
            dataset_sha256=digest,
            instances=MappingProxyType(instances),
            instance_ids=tuple(instance_ids),
        )

    def require(self, instance_id: str) -> SweBenchProInstance:
        try:
            return self.instances[instance_id]
        except KeyError as error:
            raise CatalogError(f"Unknown SWE-bench Pro instance: {instance_id}") from error
