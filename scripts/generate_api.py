"""Generate Python API data and operation metadata from OpenAPI."""

from __future__ import annotations

import argparse
from pathlib import Path
from pprint import pformat

import yaml
from datamodel_code_generator import GenerateConfig, InputFileType, generate
from datamodel_code_generator.enums import StrictTypes
from datamodel_code_generator.format import CodeFormatter, Formatter, PythonVersion
from fastapi.openapi.models import MediaType, OpenAPI, PathItem, Reference, RequestBody, Response, Schema
from fastapi.openapi.models import Operation as OpenAPIOperation
from pydantic import JsonValue, TypeAdapter, ValidationError

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "openapi" / "powercontext.yaml"
GENERATED_DIR = ROOT / "src" / "powercontext" / "http" / "_generated"
MODELS_PATH = GENERATED_DIR / "models.py"
OPERATIONS_PATH = GENERATED_DIR / "operations.py"
SCHEMA_PATH = GENERATED_DIR / "schema.py"
DRIFT_MESSAGE = "Generated API code drifted; run 'make api-generate' and review the result."
_JSON_OBJECT_ADAPTER = TypeAdapter(dict[str, JsonValue])
_MAX_CANDIDATE_EVIDENCE = 32
_CANDIDATE_EVIDENCE_VALIDATOR = f"""
    @model_validator(mode="after")
    def _reject_excess_candidate_evidence(self):
        if len(self.source_refs) + len(self.artifact_refs) > {_MAX_CANDIDATE_EVIDENCE}:
            raise ValueError(  # noqa: TRY003
                "source_refs and artifact_refs together must not exceed {_MAX_CANDIDATE_EVIDENCE} references"
            )
        return self
"""


class ContractGenerationError(RuntimeError):
    """Raised when the contract exceeds the supported generation boundary."""

    def __init__(self, subject: str, value: object) -> None:
        self.subject = subject
        self.value = value
        super().__init__(f"cannot generate PowerContext API: invalid {subject}: {value!r}")


def generate_sources() -> dict[Path, str]:
    """Build every generated source without modifying the worktree."""

    try:
        contract = OpenAPI.model_validate(yaml.safe_load(CONTRACT_PATH.read_text()))
    except ValidationError as exc:
        raise ContractGenerationError(  # noqa: TRY003
            "OpenAPI contract",
            exc.errors(include_url=False),
        ) from exc

    if contract.components is None or contract.components.schemas is None:
        raise ContractGenerationError("components.schemas", None)
    contract_data = _JSON_OBJECT_ADAPTER.validate_python(
        contract.model_dump(mode="json", by_alias=True, exclude_none=True)
    )
    return {
        MODELS_PATH: _generate_models(contract),
        OPERATIONS_PATH: _generate_operations(contract, contract.components.schemas),
        SCHEMA_PATH: _generate_schema(contract_data),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="Fail when generated API code has drifted.")
    args = parser.parse_args()
    sources = generate_sources()

    if args.check:
        if any(not path.exists() or path.read_text() != source for path, source in sources.items()):
            raise SystemExit(DRIFT_MESSAGE)
        return

    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    for path, source in sources.items():
        path.write_text(source)


def _generate_models(
    contract: OpenAPI,
) -> str:
    transport_contract = contract.model_copy(deep=True)
    transport_contract.paths = {}
    if transport_contract.components is None or transport_contract.components.schemas is None:
        raise ContractGenerationError("components.schemas", None)
    result = generate(
        transport_contract.model_dump(mode="json", by_alias=True, exclude_none=True),
        config=GenerateConfig(
            input_filename="openapi/powercontext.yaml",
            input_file_type=InputFileType.OpenAPI,
            target_python_version=PythonVersion.PY_311,
            disable_timestamp=True,
            capitalise_enum_members=True,
            field_constraints=True,
            set_default_enum_member=True,
            strict_nullable=True,
            strict_types=list(StrictTypes),
            use_standard_collections=True,
            use_union_operator=True,
            use_annotated=True,
            formatters=[Formatter.RUFF_FORMAT, Formatter.RUFF_CHECK],
        ),
    )
    if not isinstance(result, str):
        raise ContractGenerationError("model generator output", result)  # noqa: TRY003
    evidence_models = _candidate_evidence_models(transport_contract.components.schemas)
    return _with_candidate_evidence_limits(f"{result.rstrip()}\n", evidence_models)


def _generate_operations(
    contract: OpenAPI,
    schemas: dict[str, Schema | Reference],
) -> str:
    imports: set[tuple[str, str]] = set()
    operations: list[str] = []
    for path, path_item in (contract.paths or {}).items():
        if not isinstance(path_item, PathItem):
            raise ContractGenerationError("path item", path)  # noqa: TRY003
        for method, operation in path_item:
            if not isinstance(operation, OpenAPIOperation):
                continue
            if operation.operationId is None or operation.summary is None:
                raise ContractGenerationError("operation metadata", path)  # noqa: TRY003
            operation_id = operation.operationId
            request_model = _request_model(operation.requestBody, schemas)
            if request_model is not None:
                imports.add(request_model[:2])

            success_status, success_response = _success_response(operation.responses, path)
            response_model = _model_for_json_content(success_response.content, schemas, path)
            imports.add(response_model[:2])
            operations.append(
                _render_operation(
                    constant_name=operation_id.upper(),
                    method=method.upper(),
                    path=path,
                    operation_id=operation_id,
                    request_model=None if request_model is None else request_model[1],
                    response_model=response_model[1],
                    success_status=success_status,
                    summary=operation.summary,
                    tags=tuple(operation.tags or ()),
                    responses={
                        int(code) if code.isdecimal() else code: _response_metadata(response)
                        for code, response in operation.responses.items()
                    },
                )
            )

    import_lines = "\n".join(f"from {module} import {name}" for module, name in sorted(imports))
    rendered_operations = "\n\n".join(operations)
    source = f"""# generated from openapi/powercontext.yaml; do not edit.

from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, JsonValue

{import_lines}

OPENAPI_VERSION = {contract.openapi!r}
API_TITLE = {contract.info.title!r}
API_DESCRIPTION = {contract.info.description!r}
API_VERSION = {contract.info.version!r}

RequestT = TypeVar("RequestT")
ResponseT = TypeVar("ResponseT")


class Operation(BaseModel, Generic[RequestT, ResponseT]):
    method: str
    path: str
    operation_id: str
    request_type: type[RequestT] | None
    response_type: type[ResponseT]
    success_status: int
    summary: str
    tags: tuple[str, ...]
    responses: dict[int | str, dict[str, JsonValue]]


{rendered_operations}
"""
    formatter = CodeFormatter(
        python_version=PythonVersion.PY_311,
        formatters=[Formatter.RUFF_FORMAT, Formatter.RUFF_CHECK],
        settings_path=ROOT,
        encoding="utf-8",
    )
    return f"{formatter.format_code(source).rstrip()}\n"


def _generate_schema(contract: dict[str, JsonValue]) -> str:
    source = f"""# generated from openapi/powercontext.yaml; do not edit.

from pydantic import JsonValue

OPENAPI_SCHEMA: dict[str, JsonValue] = {pformat(contract, width=100, sort_dicts=False)}
"""
    formatter = CodeFormatter(
        python_version=PythonVersion.PY_311,
        formatters=[Formatter.RUFF_FORMAT, Formatter.RUFF_CHECK],
        settings_path=ROOT,
        encoding="utf-8",
    )
    return f"{formatter.format_code(source).rstrip()}\n"


def _candidate_evidence_models(schemas: dict[str, Schema | Reference]) -> tuple[str, ...]:
    """Schemas where OpenAPI caps each evidence array at 32 and the combined total is also 32."""

    names: list[str] = []
    for name, schema in schemas.items():
        if not isinstance(schema, Schema) or schema.properties is None:
            continue
        source_refs = schema.properties.get("source_refs")
        artifact_refs = schema.properties.get("artifact_refs")
        if not isinstance(source_refs, Schema) or not isinstance(artifact_refs, Schema):
            continue
        if source_refs.maxItems == _MAX_CANDIDATE_EVIDENCE and artifact_refs.maxItems == _MAX_CANDIDATE_EVIDENCE:
            names.append(name)
    return tuple(names)


def _with_candidate_evidence_limits(source: str, model_names: tuple[str, ...]) -> str:
    """Inject the combined evidence-limit validator OpenAPI cannot express natively."""

    if not model_names:
        return source
    updated = source.replace(
        "from pydantic import BaseModel, ConfigDict, Field,",
        "from pydantic import BaseModel, ConfigDict, Field, model_validator,",
        1,
    )
    if updated == source:
        raise ContractGenerationError("pydantic import line", source.splitlines()[0:20])  # noqa: TRY003
    for model_name in model_names:
        class_header = f"class {model_name}(BaseModel):"
        start = updated.find(class_header)
        if start < 0:
            raise ContractGenerationError("generated model class", model_name)  # noqa: TRY003
        next_class = updated.find("\nclass ", start + len(class_header))
        insert_at = next_class if next_class >= 0 else len(updated.rstrip())
        updated = f"{updated[:insert_at].rstrip()}\n{_CANDIDATE_EVIDENCE_VALIDATOR.rstrip()}\n\n{updated[insert_at:].lstrip()}"
    formatter = CodeFormatter(
        python_version=PythonVersion.PY_311,
        formatters=[Formatter.RUFF_FORMAT, Formatter.RUFF_CHECK],
        settings_path=ROOT,
        encoding="utf-8",
    )
    return f"{formatter.format_code(updated).rstrip()}\n"


def _request_model(
    request_body: RequestBody | Reference | None,
    schemas: dict[str, Schema | Reference],
) -> tuple[str, str] | None:
    if request_body is None:
        return None
    if not isinstance(request_body, RequestBody):
        raise ContractGenerationError("request body reference", request_body)  # noqa: TRY003
    return _model_for_json_content(request_body.content, schemas, "request body")


def _success_response(
    responses: dict[str, Response | object],
    path: str,
) -> tuple[int, Response]:
    successes = [
        (int(code), response) for code, response in responses.items() if code.isdecimal() and 200 <= int(code) < 300
    ]
    if len(successes) != 1:
        raise ContractGenerationError("success response", path)  # noqa: TRY003
    success_status, response = successes[0]
    if not isinstance(response, Response):
        raise ContractGenerationError("success response reference", path)  # noqa: TRY003
    return success_status, response


def _model_for_json_content(
    content: dict[str, MediaType] | None,
    schemas: dict[str, Schema | Reference],
    subject: str,
) -> tuple[str, str]:
    if content is None or "application/json" not in content:
        raise ContractGenerationError("application/json content", subject)  # noqa: TRY003
    response_schema = content["application/json"].schema_
    if response_schema is None or response_schema.ref is None:
        raise ContractGenerationError("schema reference", subject)  # noqa: TRY003
    schema_ref = response_schema.ref
    schema_name = schema_ref.removeprefix("#/components/schemas/")
    if schema_name not in schemas:
        raise ContractGenerationError("schema reference", schema_ref)  # noqa: TRY003
    return "powercontext.http._generated.models", schema_name


def _response_metadata(response: Response | object) -> dict[str, JsonValue]:
    if not isinstance(response, Response):
        return _JSON_OBJECT_ADAPTER.validate_python(response)
    return _JSON_OBJECT_ADAPTER.validate_python(
        response.model_dump(mode="json", by_alias=True, exclude_none=True, exclude={"content"})
    )


def _render_operation(
    *,
    constant_name: str,
    method: str,
    path: str,
    operation_id: str,
    request_model: str | None,
    response_model: str,
    success_status: int,
    summary: str,
    tags: tuple[str, ...],
    responses: dict[int | str, dict[str, JsonValue]],
) -> str:
    request_type = "None" if request_model is None else request_model
    return f"""{constant_name} = Operation[{request_type}, {response_model}](
    method={method!r},
    path={path!r},
    operation_id={operation_id!r},
    request_type={request_type},
    response_type={response_model},
    success_status={success_status},
    summary={summary!r},
    tags={tags!r},
    responses={pformat(responses, width=100, sort_dicts=False)},
)"""


if __name__ == "__main__":
    main()
