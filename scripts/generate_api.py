"""Generate Python API data and operation metadata from OpenAPI."""

from __future__ import annotations

import argparse
from pathlib import Path
from pprint import pformat

import yaml
from datamodel_code_generator import GenerateConfig, InputFileType, generate
from datamodel_code_generator.format import CodeFormatter, Formatter, PythonVersion
from fastapi.openapi.models import MediaType, OpenAPI, PathItem, Reference, Response, Schema
from fastapi.openapi.models import Operation as OpenAPIOperation
from pydantic import ImportString, JsonValue, TypeAdapter, ValidationError

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "openapi" / "powercontext.yaml"
GENERATED_DIR = ROOT / "src" / "powercontext" / "api" / "generated"
MODELS_PATH = GENERATED_DIR / "models.py"
OPERATIONS_PATH = GENERATED_DIR / "operations.py"
SCHEMA_PATH = GENERATED_DIR / "schema.py"
CORE_MODEL_KEY = "x-powercontext-python-model"
DATAMODEL_IMPORT_KEY = "x-python-import"
DRIFT_MESSAGE = "Generated API code drifted; run 'make api-generate' and review the result."
_CORE_MODEL_ADAPTER = TypeAdapter(ImportString[type[object]])
_JSON_OBJECT_ADAPTER = TypeAdapter(dict[str, JsonValue])
_RESPONSES_ADAPTER = TypeAdapter(dict[str, Response])


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
    core_models: dict[str, type[object]] = {}
    for name, schema in contract.components.schemas.items():
        model_reference = (schema.model_extra or {}).get(CORE_MODEL_KEY)
        if model_reference is not None:
            core_models[name] = _CORE_MODEL_ADAPTER.validate_python(model_reference)
    contract_data = _JSON_OBJECT_ADAPTER.validate_python(
        contract.model_dump(mode="json", by_alias=True, exclude_none=True)
    )
    return {
        MODELS_PATH: _generate_models(contract, core_models),
        OPERATIONS_PATH: _generate_operations(contract, contract.components.schemas, core_models),
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
    core_models: dict[str, type[object]],
) -> str:
    transport_contract = contract.model_copy(deep=True)
    transport_contract.paths = {}
    if transport_contract.components is None or transport_contract.components.schemas is None:
        raise ContractGenerationError("components.schemas", None)
    for name, model in core_models.items():
        schema = transport_contract.components.schemas[name]
        if schema.model_extra is None:
            raise ContractGenerationError("Core model schema", name)  # noqa: TRY003
        schema.model_extra[DATAMODEL_IMPORT_KEY] = {
            "module": model.__module__,
            "name": model.__name__,
        }
    result = generate(
        transport_contract.model_dump(mode="json", by_alias=True, exclude_none=True),
        config=GenerateConfig(
            input_filename="openapi/powercontext.yaml",
            input_file_type=InputFileType.OpenAPI,
            target_python_version=PythonVersion.PY_311,
            disable_timestamp=True,
            enable_faux_immutability=True,
            capitalise_enum_members=True,
            use_standard_collections=True,
            use_union_operator=True,
            formatters=[Formatter.RUFF_FORMAT, Formatter.RUFF_CHECK],
        ),
    )
    if not isinstance(result, str):
        raise ContractGenerationError("model generator output", result)  # noqa: TRY003
    return f"{result.rstrip()}\n"


def _generate_operations(
    contract: OpenAPI,
    schemas: dict[str, Schema | Reference],
    core_models: dict[str, type[object]],
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
            responses = _RESPONSES_ADAPTER.validate_python(operation.responses)
            success_response = responses["200"]
            if success_response.content is None:
                raise ContractGenerationError("200 response content", path)  # noqa: TRY003
            json_response: MediaType = success_response.content["application/json"]
            response_schema = json_response.schema_
            if response_schema is None or response_schema.ref is None:
                raise ContractGenerationError("200 response schema", path)  # noqa: TRY003
            schema_ref = response_schema.ref
            schema_name = schema_ref.removeprefix("#/components/schemas/")
            if schema_name not in schemas:
                raise ContractGenerationError("response schema reference", schema_ref)  # noqa: TRY003

            response_model = core_models.get(schema_name)
            module = response_model.__module__ if response_model is not None else "powercontext.api.generated.models"
            model_name = response_model.__name__ if response_model is not None else schema_name
            imports.add((module, model_name))
            operations.append(
                _render_operation(
                    constant_name=operation_id.upper(),
                    method=method.upper(),
                    path=path,
                    operation_id=operation_id,
                    response_model=model_name,
                    summary=operation.summary,
                    tags=tuple(operation.tags or ()),
                    responses={
                        int(code) if code.isdecimal() else code: _response_metadata(response)
                        for code, response in responses.items()
                    },
                )
            )

    import_lines = "\n".join(f"from {module} import {name}" for module, name in sorted(imports))
    rendered_operations = "\n\n".join(operations)
    source = f"""# generated from openapi/powercontext.yaml; do not edit.

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar

from pydantic import JsonValue

{import_lines}

OPENAPI_VERSION = {contract.openapi!r}
API_TITLE = {contract.info.title!r}
API_DESCRIPTION = {contract.info.description!r}
API_VERSION = {contract.info.version!r}

ResponseT = TypeVar("ResponseT")


@dataclass(frozen=True, slots=True, kw_only=True)
class Operation(Generic[ResponseT]):
    method: str
    path: str
    operation_id: str
    response_type: type[ResponseT]
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


def _response_metadata(response: Response) -> dict[str, JsonValue]:
    return _JSON_OBJECT_ADAPTER.validate_python(
        response.model_dump(mode="json", by_alias=True, exclude_none=True, exclude={"content"})
    )


def _render_operation(
    *,
    constant_name: str,
    method: str,
    path: str,
    operation_id: str,
    response_model: str,
    summary: str,
    tags: tuple[str, ...],
    responses: dict[int | str, dict[str, JsonValue]],
) -> str:
    return f"""{constant_name} = Operation[{response_model}](
    method={method!r},
    path={path!r},
    operation_id={operation_id!r},
    response_type={response_model},
    summary={summary!r},
    tags={tags!r},
    responses={pformat(responses, width=100, sort_dicts=False)},
)"""


if __name__ == "__main__":
    main()
