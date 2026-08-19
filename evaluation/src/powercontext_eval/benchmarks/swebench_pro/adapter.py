"""Normalized SWE-bench Pro instance contracts."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from powercontext_eval.errors import PowerContextEvalError

HARNESS_COMMIT = "ca10a60a5fcae51e6948ffe1485d4153d421e6c5"
DATASET_REVISION = "7ab5114912baf22bb098818e604c02fe7ad2c11f"

_LEGACY_FIELDS = frozenset(
    {
        "repo",
        "instance_id",
        "base_commit",
        "patch",
        "test_patch",
        "problem_statement",
        "requirements",
        "interface",
        "repo_language",
        "fail_to_pass",
        "pass_to_pass",
        "issue_specificity",
        "issue_categories",
        "before_repo_set_cmd",
        "selected_test_files_to_run",
        "dockerhub_tag",
    }
)
PUBLIC_FIELDS = frozenset(
    {
        "FAIL_TO_PASS",
        "PASS_TO_PASS",
        "base_commit",
        "base_dockerfile",
        "before_repo_set_cmd",
        "created_at",
        "hints_text",
        "image_name",
        "instance_dockerfile",
        "instance_id",
        "is_remote_image",
        "parsing_script",
        "patch",
        "problem_statement",
        "repo",
        "repo_name",
        "run_script",
        "selected_test_files_to_run",
        "test_patch",
        "version",
    }
)
_PUBLIC_STRING_FIELDS = PUBLIC_FIELDS - {"FAIL_TO_PASS", "PASS_TO_PASS", "is_remote_image"}
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_PUBLIC_IMAGE = re.compile(
    r"^[0-9]{12}\.dkr\.ecr\.[a-z0-9-]+\.amazonaws\.com/"
    r"sweap-images/(?P<repository>[A-Za-z0-9_.-]+):(?P<tag>[A-Za-z0-9_.-]+)$"
)
_ELEMENT_WEB_VNAN_INSTANCE = "instance_element-hq__element-web-ec0f940ef0e8e3b61078f145f34dc40d1938e6c5-vnan"
_DOCKER_TAG_LIMIT = 128


class DatasetSchemaError(PowerContextEvalError):
    """A pinned dataset row does not match the expected public schema."""


@dataclass(frozen=True)
class SweBenchProInstance:
    """One immutable, normalized SWE-bench Pro instance."""

    repo: str
    instance_id: str
    base_commit: str
    patch: str
    test_patch: str
    problem_statement: str
    fail_to_pass: tuple[str, ...]
    pass_to_pass: tuple[str, ...]
    before_repo_set_cmd: str
    selected_test_files_to_run: str
    task_image: str
    raw_row: Mapping[str, object]
    requirements: str = ""
    interface: str = ""
    docker_manifest_digest: str | None = None

    @classmethod
    def from_public_raw(cls, raw: Mapping[str, object]) -> SweBenchProInstance:
        """Validate and normalize one row from the official public-v2 file."""

        _require_exact_fields(raw, PUBLIC_FIELDS)
        invalid_strings = sorted(key for key in _PUBLIC_STRING_FIELDS if not isinstance(raw[key], str))
        if invalid_strings:
            raise DatasetSchemaError(f"Dataset row fields must be strings: {', '.join(invalid_strings)}")
        if not isinstance(raw["is_remote_image"], bool):
            raise DatasetSchemaError("Dataset row field is_remote_image must be a boolean")

        instance_id = _nonblank(raw["instance_id"], "instance_id")
        task_image = _docker_hub_image(_nonblank(raw["image_name"], "image_name"), instance_id=instance_id)
        fail_to_pass = _test_names(raw["FAIL_TO_PASS"], "FAIL_TO_PASS")
        pass_to_pass = _test_names(raw["PASS_TO_PASS"], "PASS_TO_PASS")
        preserved = MappingProxyType(
            {key: tuple(value) if isinstance(value, list) else value for key, value in sorted(raw.items())}
        )
        return cls(
            repo=_nonblank(raw["repo"], "repo"),
            instance_id=instance_id,
            base_commit=_commit(raw["base_commit"]),
            patch=str(raw["patch"]),
            test_patch=str(raw["test_patch"]),
            problem_statement=_nonblank(raw["problem_statement"], "problem_statement"),
            fail_to_pass=fail_to_pass,
            pass_to_pass=pass_to_pass,
            before_repo_set_cmd=str(raw["before_repo_set_cmd"]),
            selected_test_files_to_run=str(raw["selected_test_files_to_run"]),
            task_image=task_image,
            raw_row=preserved,
        )

    @classmethod
    def from_raw(
        cls,
        raw: Mapping[str, object],
        *,
        docker_manifest_digest: str,
    ) -> SweBenchProInstance:
        """Load the original one-row transformed format for compatibility."""

        _require_exact_fields(raw, _LEGACY_FIELDS)
        invalid = sorted(key for key in _LEGACY_FIELDS if not isinstance(raw[key], str))
        if invalid:
            raise DatasetSchemaError(f"Dataset row fields must be strings: {', '.join(invalid)}")
        if _DIGEST.fullmatch(docker_manifest_digest) is None:
            raise DatasetSchemaError("Docker manifest digest must be an exact sha256 digest")
        values = {key: str(raw[key]) for key in _LEGACY_FIELDS}
        return cls(
            repo=_nonblank(values["repo"], "repo"),
            instance_id=_nonblank(values["instance_id"], "instance_id"),
            base_commit=_commit(values["base_commit"]),
            patch=values["patch"],
            test_patch=values["test_patch"],
            problem_statement=_nonblank(values["problem_statement"], "problem_statement"),
            fail_to_pass=_test_names(values["fail_to_pass"], "fail_to_pass"),
            pass_to_pass=_test_names(values["pass_to_pass"], "pass_to_pass"),
            before_repo_set_cmd=values["before_repo_set_cmd"],
            selected_test_files_to_run=values["selected_test_files_to_run"],
            task_image=f"jefzda/sweap-images:{values['dockerhub_tag']}",
            raw_row=MappingProxyType(dict(sorted(values.items()))),
            requirements=values["requirements"],
            interface=values["interface"],
            docker_manifest_digest=docker_manifest_digest,
        )

    def codex_prompt(self) -> str:
        """Render only fields visible to the coding agent."""

        prompt = f"Solve the following repository task.\n\nProblem statement:\n{self.problem_statement}\n"
        if self.requirements:
            prompt += f"\nRequirements:\n{self.requirements}\n"
        if self.interface:
            prompt += f"\nInterface:\n{self.interface}\n"
        return prompt

    def official_row(self) -> dict[str, Any]:
        """Return an independent JSON-compatible copy of the pinned source row."""

        return {key: list(value) if isinstance(value, tuple) else value for key, value in self.raw_row.items()}


def _require_exact_fields(raw: Mapping[str, object], expected: frozenset[str]) -> None:
    missing = sorted(expected - set(raw))
    unexpected = sorted(set(raw) - expected)
    if missing:
        raise DatasetSchemaError(f"Dataset row has missing fields: {', '.join(missing)}")
    if unexpected:
        raise DatasetSchemaError(f"Dataset row has unexpected fields: {', '.join(unexpected)}")


def _nonblank(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DatasetSchemaError(f"Dataset row field {field} must be a non-blank string")
    return value


def _commit(value: object) -> str:
    commit = _nonblank(value, "base_commit")
    if re.fullmatch(r"[0-9a-f]{40}", commit) is None:
        raise DatasetSchemaError("Dataset row field base_commit must be a lowercase 40-character Git SHA")
    return commit


def _test_names(value: object, field: str) -> tuple[str, ...]:
    parsed = value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as error:
            raise DatasetSchemaError(f"Dataset row field {field} must contain a JSON array") from error
    if not isinstance(parsed, list):
        raise DatasetSchemaError(f"Dataset row field {field} must be an array of non-blank strings")
    names: list[str] = []
    for item in parsed:
        if not isinstance(item, str) or not item:
            raise DatasetSchemaError(f"Dataset row field {field} must be an array of non-blank strings")
        names.append(item)
    return tuple(names)


def _docker_hub_image(image_name: str, *, instance_id: str) -> str:
    matched = _PUBLIC_IMAGE.fullmatch(image_name)
    if matched is None:
        raise DatasetSchemaError("Dataset row field image_name is not a recognized SWE-bench Pro ECR image")
    if instance_id == _ELEMENT_WEB_VNAN_INSTANCE:
        tag = f"element-hq.element-web-{instance_id.removeprefix('instance_')}"
    else:
        tag = f"{matched.group('repository')}-{matched.group('tag')}"
    return f"jefzda/sweap-images:{tag[:_DOCKER_TAG_LIMIT]}"
