import pytest
from pydantic import ValidationError

from powercontext.api import Capabilities


def test_capabilities_require_the_complete_transport_shape() -> None:
    with pytest.raises(ValidationError):
        Capabilities.model_validate({"source_types": [], "artifact_families": [], "search_modes": []})


def test_capabilities_reject_unknown_transport_fields() -> None:
    with pytest.raises(ValidationError):
        Capabilities.model_validate({
            "source_types": [],
            "artifact_families": [],
            "search_modes": [],
            "limits": [],
            "runtime_internal": True,
        })
