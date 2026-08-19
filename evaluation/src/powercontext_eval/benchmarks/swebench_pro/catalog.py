"""Immutable catalog for the pinned public SWE-bench Pro v2 task set."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Literal, Self, TypeAlias

from powercontext_eval.benchmarks.swebench_pro.adapter import DatasetSchemaError, SweBenchProInstance
from powercontext_eval.errors import PowerContextEvalError

PUBLIC_V2_COUNT = 731
PUBLIC_V2_SHA256 = "b5b2462bfbf5aeb2cb7ba7d215778a1768b85f9d7ad7f748546c7f80a0ad1510"
PUBLIC_V2_TASK_SET = "swebench-pro-public-v2"
STABILITY_V1_TASK_SET = "swebench-pro-stability-v1"
TaskSet: TypeAlias = Literal["swebench-pro-public-v2", "swebench-pro-stability-v1"]

# One saturated 20-task wave plus four queued replacements.  Each entry pins both
# the public-v2 source index and instance ID so a reordered or replaced dataset
# fails closed instead of silently changing the regression suite.
STABILITY_V1_CASES: tuple[tuple[int, str], ...] = (
    (101, "instance_element-hq__element-web-4c6b0d35add7ae8d58f71ea1711587e31081444b-vnan"),
    (450, "instance_NodeBB__NodeBB-00c70ce7b0541cfc94afe567921d7668cdc8f4ac-vnan"),
    (
        463,
        "instance_ansible__ansible-185d41031660a676c43fbb781cd1335902024bfe-vba6da65a0f3baefda7a058ebbd0a8dcafb8512f5",
    ),
    (
        472,
        "instance_tutao__tutanota-51818218c6ae33de00cbea3a4d30daac8c34142e-vc4e41fd0029957297843cb9dec4a25c7c756f029",
    ),
    (
        498,
        "instance_internetarchive__openlibrary-b67138b316b1e9c11df8a4a8391fe5cc8e75ff9f-ve8c8d62a2b60610a3c4631f5f23ed866bada9818",
    ),
    (551, "instance_flipt-io__flipt-abaa5953795afb9c621605bb18cb32ac48b4508c"),
    (559, "instance_gravitational__teleport-c335534e02de143508ebebc7341021d7f8656e8f"),
    (214, "instance_future-architect__vuls-ca3f6b1dbf2cd24d1537bfda43e788443ce03a0c"),
    (402, "instance_navidrome__navidrome-677d9947f302c9f7bba8c08c788c3dc99f235f39"),
    (357, "instance_protonmail__webclients-c5a2089ca2bfe9aa1d85a664b8ad87ef843a1c9c"),
    (
        159,
        "instance_qutebrowser__qutebrowser-44e64199ed38003253f0296badd4a447645067b6-v2ef375ac784985212b1805e1d0431dc8f1b3c171",
    ),
    (
        374,
        "instance_NodeBB__NodeBB-8168c6c40707478f71b8af60300830fe554c778c-vf2cf3cbd463b7ad942381f1c6d077626485a1e9e",
    ),
    (
        568,
        "instance_ansible__ansible-bec27fb4c0a40c5f8bbcf26a475704227d65ee73-v30a923fb5c164d6cd18280c02422f75e611e8fb2",
    ),
    (697, "instance_element-hq__element-web-aeabf3b18896ac1eb7ae9757e66ce886120f8309-vnan"),
    (310, "instance_protonmail__webclients-4feccbc9990980aee26ea29035f8f931d6089895"),
    (37, "instance_gravitational__teleport-0ac7334939981cf85b9591ac295c3816954e287e"),
    (
        175,
        "instance_tutao__tutanota-f373ac3808deefce8183dad8d16729839cc330c1-v2939aa9f4356f0dc9f523ee5ce19d09e08ab979b",
    ),
    (457, "instance_flipt-io__flipt-b4bb5e13006a729bc0eed8fe6ea18cff54acdacb"),
    (
        438,
        "instance_internetarchive__openlibrary-d40ec88713dc95ea791b252f92d2f7b75e107440-v13642507b4fc1f8d234172bf8129942da2c2ca26",
    ),
    (
        460,
        "instance_ansible__ansible-8127abbc298cabf04aaa89a478fc5e5e3432a6fc-v30a923fb5c164d6cd18280c02422f75e611e8fb2",
    ),
    (200, "instance_navidrome__navidrome-5e549255201e622c911621a7b770477b1f5a89be"),
    (650, "instance_protonmail__webclients-cba6ebbd0707caa524ffee51c62b197f6122c902"),
    (
        197,
        "instance_qutebrowser__qutebrowser-ed19d7f58b2664bb310c7cb6b52c5b9a06ea60b2-v059c6fdc75567943479b23ebca7c07b5e9a7f34c",
    ),
    (
        526,
        "instance_internetarchive__openlibrary-757fcf46c70530739c150c57b37d6375f155dc97-ve8c8d62a2b60610a3c4631f5f23ed866bada9818",
    ),
)
STABILITY_V1_COUNT = len(STABILITY_V1_CASES)


class CatalogError(PowerContextEvalError):
    """The pinned task-set file cannot be trusted or indexed."""


def instance_ids_for_task_set(instance_ids: tuple[str, ...], task_set: TaskSet) -> tuple[str, ...]:
    """Select one immutable task set while validating its pinned source rows."""

    if task_set == PUBLIC_V2_TASK_SET:
        return instance_ids
    selected: list[str] = []
    for source_index, instance_id in STABILITY_V1_CASES:
        if source_index >= len(instance_ids) or instance_ids[source_index] != instance_id:
            raise CatalogError(f"{STABILITY_V1_TASK_SET} no longer matches public-v2 source index {source_index}")
        selected.append(instance_id)
    return tuple(selected)


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

    def instance_ids_for(self, task_set: TaskSet) -> tuple[str, ...]:
        return instance_ids_for_task_set(self.instance_ids, task_set)
