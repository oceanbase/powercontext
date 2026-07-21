from pathlib import Path

import yaml

import powercontext
from powercontext.api import Capabilities
from powercontext.api.generated.operations import GET_CAPABILITIES, GET_LIVENESS, GET_READINESS
from powercontext.server.app import create_app

CONTRACT_PATH = Path(__file__).resolve().parents[1] / "openapi" / "powercontext.yaml"


def test_operations_are_generated_from_the_contract() -> None:
    operations = (GET_LIVENESS, GET_READINESS, GET_CAPABILITIES)

    assert [(operation.method, operation.path, operation.operation_id) for operation in operations] == [
        ("GET", "/health/live", "get_liveness"),
        ("GET", "/health/ready", "get_readiness"),
        ("GET", "/v1/capabilities", "get_capabilities"),
    ]


def test_capabilities_operation_uses_the_generated_transport_model() -> None:
    assert GET_CAPABILITIES.response_type is Capabilities


def test_readiness_operation_declares_the_unavailable_response() -> None:
    assert 503 in GET_READINESS.responses


def test_server_capabilities_stay_out_of_the_core_api() -> None:
    assert not hasattr(powercontext, "Capabilities")


def test_server_publishes_the_canonical_openapi_schema() -> None:
    contract = yaml.safe_load(CONTRACT_PATH.read_text())

    assert create_app().openapi() == contract
