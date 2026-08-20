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

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path

import pytest

from powercontext_eval.benchmarks.swebench_pro.adapter import SweBenchProInstance
from powercontext_eval.benchmarks.swebench_pro.catalog import (
    PUBLIC_V2_COUNT,
    PUBLIC_V2_SHA256,
    PUBLIC_V2_TASK_SET,
    STABILITY_V1_CASES,
    STABILITY_V1_COUNT,
    STABILITY_V1_TASK_SET,
    CatalogError,
    SweBenchProCatalog,
    instance_ids_for_task_set,
)

FIXTURE = Path(__file__).parent / "fixtures" / "swebench_pro_public_v2.jsonl"


def fixture_dataset(tmp_path: Path) -> Path:
    dataset = tmp_path / "sweap_eval_full_v2.jsonl"
    dataset.write_bytes(FIXTURE.read_bytes())
    return dataset


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_fixture(path: Path) -> SweBenchProCatalog:
    return SweBenchProCatalog.load(path, expected_sha256=sha256(path), expected_count=3)


def test_public_task_set_contract_is_pinned() -> None:
    assert PUBLIC_V2_TASK_SET == "swebench-pro-public-v2"
    assert PUBLIC_V2_COUNT == 731
    assert PUBLIC_V2_SHA256 == "b5b2462bfbf5aeb2cb7ba7d215778a1768b85f9d7ad7f748546c7f80a0ad1510"


def test_stability_task_set_pins_one_full_wave_and_four_replacements() -> None:
    public_ids = [f"unselected-{index}" for index in range(PUBLIC_V2_COUNT)]
    for source_index, instance_id in STABILITY_V1_CASES:
        public_ids[source_index] = instance_id

    selected = instance_ids_for_task_set(tuple(public_ids), STABILITY_V1_TASK_SET)

    assert STABILITY_V1_COUNT == 24
    assert selected == tuple(instance_id for _, instance_id in STABILITY_V1_CASES)
    assert len(set(selected)) == STABILITY_V1_COUNT
    assert instance_ids_for_task_set(tuple(public_ids), PUBLIC_V2_TASK_SET) == tuple(public_ids)


def test_stability_task_set_fails_closed_when_a_pinned_source_row_drifts() -> None:
    public_ids = [f"unselected-{index}" for index in range(PUBLIC_V2_COUNT)]
    for source_index, instance_id in STABILITY_V1_CASES:
        public_ids[source_index] = instance_id
    public_ids[STABILITY_V1_CASES[0][0]] = "replacement"

    with pytest.raises(CatalogError, match="source index 101"):
        instance_ids_for_task_set(tuple(public_ids), STABILITY_V1_TASK_SET)


def test_catalog_loads_pinned_rows_in_source_order_and_normalizes_lists(tmp_path: Path) -> None:
    dataset = fixture_dataset(tmp_path)

    catalog = load_fixture(dataset)

    assert catalog.dataset_path == dataset
    assert catalog.dataset_sha256 == sha256(dataset)
    assert catalog.instance_ids == (
        "instance_owner__repo-a",
        "instance_owner__repo-b",
        "instance_owner__repo-c",
    )
    first = catalog.require("instance_owner__repo-a")
    second = catalog.require("instance_owner__repo-b")
    assert first.fail_to_pass == ("test_fix",)
    assert first.pass_to_pass == ("test_regression",)
    assert second.fail_to_pass == ("test_b",)
    assert second.pass_to_pass == ()
    assert first.task_image == "jefzda/sweap-images:owner.repo-owner__repo-a"


def test_public_image_tag_is_truncated_to_the_docker_tag_limit() -> None:
    raw = json.loads(FIXTURE.read_text().splitlines()[0])
    raw["repo"] = "qutebrowser/qutebrowser"
    raw["instance_id"] = (
        "instance_qutebrowser__qutebrowser-f91ace96223cac8161c16dd061907e138fe85111"
        "-v059c6fdc75567943479b23ebca7c07b5e9a7f34c"
    )
    raw["image_name"] = (
        "084828598639.dkr.ecr.us-west-2.amazonaws.com/sweap-images/"
        "qutebrowser.qutebrowser:"
        "qutebrowser__qutebrowser-f91ace96223cac8161c16dd061907e138fe85111"
        "-v059c6fdc75567943479b23ebca7c07b5e9a7f34c"
    )

    image = SweBenchProInstance.from_public_raw(raw).task_image

    assert image == (
        "jefzda/sweap-images:"
        "qutebrowser.qutebrowser-qutebrowser__qutebrowser-f91ace96223cac8161c16dd061907e138fe85111"
        "-v059c6fdc75567943479b23ebca7c07b5e9a7f"
    )
    assert len(image.rsplit(":", 1)[1]) == 128


def test_public_image_preserves_the_official_element_web_exception() -> None:
    raw = json.loads(FIXTURE.read_text().splitlines()[0])
    raw["repo"] = "element-hq/element-web"
    raw["instance_id"] = "instance_element-hq__element-web-ec0f940ef0e8e3b61078f145f34dc40d1938e6c5-vnan"
    raw["image_name"] = (
        "084828598639.dkr.ecr.us-west-2.amazonaws.com/sweap-images/"
        "element-hq.element:element-hq__element-web-ec0f940ef0e8e3b61078f145f34dc40d1938e6c5"
    )

    image = SweBenchProInstance.from_public_raw(raw).task_image

    assert image == (
        "jefzda/sweap-images:"
        "element-hq.element-web-element-hq__element-web-ec0f940ef0e8e3b61078f145f34dc40d1938e6c5-vnan"
    )


def test_catalog_lookup_does_not_reread_source_file(tmp_path: Path) -> None:
    dataset = fixture_dataset(tmp_path)
    catalog = load_fixture(dataset)
    dataset.unlink()

    assert catalog.require("instance_owner__repo-b").problem_statement == "Fix B"
    with pytest.raises(CatalogError, match="Unknown SWE-bench Pro instance"):
        catalog.require("missing")


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda rows: [], "blank"),
        (lambda rows: [rows[0], rows[0], rows[2]], "duplicate"),
        (lambda rows: [rows[0], [], rows[2]], "JSON object"),
        (lambda rows: [rows[0], {key: value for key, value in rows[1].items() if key != "repo"}, rows[2]], "missing"),
    ],
)
def test_catalog_rejects_invalid_rows(
    tmp_path: Path,
    mutate: Callable[[list[dict[str, object]]], list[object]],
    message: str,
) -> None:
    rows: list[dict[str, object]] = [json.loads(line) for line in FIXTURE.read_text().splitlines()]
    changed = mutate(rows)
    dataset = tmp_path / "invalid.jsonl"
    dataset.write_text("".join(json.dumps(row, separators=(",", ":")) + "\n" for row in changed))

    with pytest.raises(CatalogError, match=message):
        SweBenchProCatalog.load(dataset, expected_sha256=sha256(dataset), expected_count=len(changed))


def test_catalog_enforces_exact_hash_and_count(tmp_path: Path) -> None:
    dataset = fixture_dataset(tmp_path)

    with pytest.raises(CatalogError, match="SHA-256"):
        SweBenchProCatalog.load(dataset, expected_sha256="0" * 64, expected_count=3)
    with pytest.raises(CatalogError, match="count"):
        SweBenchProCatalog.load(dataset, expected_sha256=sha256(dataset), expected_count=731)


@pytest.mark.parametrize(
    "image_name",
    [
        "docker.io/sweap-images/owner.repo:owner__repo-a",
        "084828598639.dkr.ecr.us-west-2.amazonaws.com/other/owner.repo:owner__repo-a",
        "084828598639.dkr.ecr.us-west-2.amazonaws.com/sweap-images/no-tag",
    ],
)
def test_catalog_rejects_unrecognized_image_paths(tmp_path: Path, image_name: str) -> None:
    rows = [json.loads(line) for line in FIXTURE.read_text().splitlines()]
    rows[0]["image_name"] = image_name
    dataset = tmp_path / "bad-image.jsonl"
    dataset.write_text("".join(json.dumps(row, separators=(",", ":")) + "\n" for row in rows))

    with pytest.raises(CatalogError, match="image_name"):
        SweBenchProCatalog.load(dataset, expected_sha256=sha256(dataset), expected_count=3)
