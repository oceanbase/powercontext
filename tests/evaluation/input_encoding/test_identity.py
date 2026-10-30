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

import json
import stat

import pytest

from evaluation.performance.input_encoding.identity import (
    KEY_FILENAME,
    ConfigurationIdentityError,
    prepare_configuration_identity,
)
from powercontext.builtin.runtime.config import BuiltinConfig, InferenceConfig


def _configuration(*, database="database-password", forbidden="private-account", header="header-credential"):
    return BuiltinConfig.model_validate({
        "database": {
            "kind": "oceanbase",
            "url": f"mysql+aoceanbase://user:{database}@localhost:2881/example?charset=utf8mb4",
        },
        "inference": {
            "generation_model": "openai-chat:example-model",
            "generation_headers": {"X-Private-Credential": header},
            "protection": {"forbidden_values": [forbidden]},
        },
    })


def test_identity_is_stable_on_reopen_but_not_linkable_across_output_directories(tmp_path):
    config = _configuration()
    original = prepare_configuration_identity(tmp_path / "a", {"off": config}, environment={})
    assert (
        prepare_configuration_identity(tmp_path / "a", {"off": config}, previous=original, environment={}) == original
    )
    independent = prepare_configuration_identity(tmp_path / "b", {"off": config}, environment={})
    assert original["private_config_digest"] != independent["private_config_digest"]
    assert original["masked_config_hash"] == independent["masked_config_hash"]
    assert stat.S_IMODE((tmp_path / "a" / KEY_FILENAME).stat().st_mode) == 0o600


@pytest.mark.parametrize("field", ["database", "forbidden", "header"])
def test_secret_config_rotation_rejects_resume_even_when_masked_hash_is_unchanged(tmp_path, field):
    original = prepare_configuration_identity(tmp_path / "a", {"off": _configuration()}, environment={})
    rotated = _configuration(**{field: "changed-private-value"})
    public = prepare_configuration_identity(tmp_path / "b", {"off": rotated}, environment={})
    assert public["masked_config_hash"] == original["masked_config_hash"]
    with pytest.raises(ConfigurationIdentityError, match="Private configuration changed") as captured:
        prepare_configuration_identity(tmp_path / "a", {"off": rotated}, previous=original, environment={})
    assert "changed-private-value" not in str(captured.value)


@pytest.mark.parametrize(
    ("model", "variable"),
    [
        ("openai-chat:example", "OPENAI_API_KEY"),
        ("openai:example", "OPENAI_ORG_ID"),
        ("openai-responses:example", "OPENAI_PROJECT_ID"),
        ("openai-chat:example", "OPENAI_BASE_URL"),
        ("openai-chat:example", "OPENAI_CUSTOM_HEADERS"),
        ("anthropic:example", "ANTHROPIC_API_KEY"),
        ("anthropic:example", "ANTHROPIC_AUTH_TOKEN"),
        ("anthropic:example", "ANTHROPIC_BASE_URL"),
        ("anthropic:example", "ANTHROPIC_CUSTOM_HEADERS"),
    ],
)
def test_effective_provider_environment_rotation_is_detected(tmp_path, monkeypatch, model, variable):
    config = InferenceConfig(generation_model=model)
    monkeypatch.setenv(variable, "original-provider-value")
    original = prepare_configuration_identity(tmp_path, {"judge": config})
    monkeypatch.setenv(variable, "rotated-provider-value")
    with pytest.raises(ConfigurationIdentityError, match="Private configuration changed") as captured:
        prepare_configuration_identity(tmp_path, {"judge": config}, previous=original)
    assert "rotated-provider-value" not in str(captured.value)


def test_unrelated_volatile_environment_does_not_change_identity(tmp_path):
    config = {"off": _configuration()}
    original = prepare_configuration_identity(
        tmp_path, config, environment={"OPENAI_API_KEY": "provider-key", "PWD": "a"}
    )
    assert (
        prepare_configuration_identity(
            tmp_path,
            config,
            previous=original,
            environment={"OPENAI_API_KEY": "provider-key", "PWD": "b", "UNRELATED_JOB_ID": "new"},
        )
        == original
    )


def test_saved_identity_contains_neither_plaintext_secrets_nor_provider_values(tmp_path):
    config = {"off": _configuration()}
    identity = prepare_configuration_identity(tmp_path, config, environment={"OPENAI_API_KEY": "provider-credential"})
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"configuration_identity": identity}))
    assert (
        prepare_configuration_identity(
            tmp_path, config, previous=identity, environment={"OPENAI_API_KEY": "provider-credential"}
        )
        == identity
    )
    saved = b"".join(path.read_bytes() for path in tmp_path.iterdir())
    for secret in ("database-password", "private-account", "header-credential", "provider-credential"):
        assert secret.encode() not in saved
    assert len(identity["private_config_digest"]) == 64


def test_legacy_manifest_cannot_silently_acquire_a_new_identity(tmp_path):
    (tmp_path / "manifest.json").write_text(json.dumps({"config_hashes": {"off": "masked-hash"}}))
    with pytest.raises(ConfigurationIdentityError, match="lacks private configuration identity"):
        prepare_configuration_identity(tmp_path, {"off": _configuration()}, environment={})
    assert not (tmp_path / KEY_FILENAME).exists()


@pytest.mark.parametrize("damage", ["missing", "rotated", "permissions", "symlink"])
def test_missing_or_changed_private_key_cannot_resume(tmp_path, damage):
    configurations = {"off": _configuration()}
    original = prepare_configuration_identity(tmp_path, configurations, environment={})
    key = tmp_path / KEY_FILENAME
    if damage == "missing":
        key.unlink()
    elif damage == "rotated":
        key.write_bytes(b"x" * 32)
    elif damage == "permissions":
        key.chmod(0o644)
    else:
        destination = tmp_path / "private-key-original"
        key.rename(destination)
        key.symlink_to(destination)
    with pytest.raises(ConfigurationIdentityError):
        prepare_configuration_identity(tmp_path, configurations, previous=original, environment={})
    if damage == "missing":
        assert not key.exists()


def test_uninspected_credential_chain_is_rejected_explicitly(tmp_path):
    config = InferenceConfig(generation_model="bedrock:example-model")
    with pytest.raises(ConfigurationIdentityError, match="credential identity is not supported"):
        prepare_configuration_identity(tmp_path, {"judge": config}, environment={})
    assert not (tmp_path / KEY_FILENAME).exists()


@pytest.mark.parametrize(
    "configuration",
    [
        {
            "generation_model": "anthropic:example",
            "generation_headers": {"aUtHoRiZaTiOn": "Bearer synthetic-gateway-token"},
        },
        *[
            {
                "generation_model": "openai-chat:example",
                "generation_headers": {"X-Api-Key": "synthetic-static-key"},
                f"{operation}_model": "anthropic:example",
                f"{operation}_headers": {"Authorization": "Bearer synthetic-gateway-token"},
            }
            for operation in ("rerank", "decision")
        ],
        *[
            {
                "generation_model": "anthropic:example",
                "generation_headers": {"X-Workspace": "synthetic-workspace"},
                f"{operation}_headers": {"Authorization": "Bearer synthetic-gateway-token"},
            }
            for operation in ("rerank", "decision")
        ],
    ],
)
def test_anthropic_profile_discovery_is_rejected_before_accepting_an_identity(tmp_path, configuration):
    config = InferenceConfig.model_validate(configuration)
    with pytest.raises(ConfigurationIdentityError, match="Anthropic profile discovery is not supported"):
        prepare_configuration_identity(
            tmp_path, {"judge": config}, environment={"ANTHROPIC_PROFILE": "synthetic-profile"}
        )
    assert not (tmp_path / KEY_FILENAME).exists()


@pytest.mark.parametrize("credential", ["ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "X-Api-Key"])
def test_anthropic_static_credentials_do_not_enable_profile_discovery(tmp_path, credential):
    headers = {"Authorization": "Bearer synthetic-gateway-token"}
    environment = {"ANTHROPIC_PROFILE": "first-profile"}
    if credential == "X-Api-Key":
        headers[credential] = "synthetic-static-key"
    else:
        environment[credential] = "synthetic-static-key"
    config = InferenceConfig.model_validate({"generation_model": "anthropic:example", "generation_headers": headers})
    identity = prepare_configuration_identity(tmp_path, {"judge": config}, environment=environment)
    environment["ANTHROPIC_PROFILE"] = "second-profile"
    assert (
        prepare_configuration_identity(tmp_path, {"judge": config}, previous=identity, environment=environment)
        == identity
    )


@pytest.mark.parametrize("operation", ["rerank", "decision"])
def test_inherited_anthropic_headers_keep_the_bound_static_credential(tmp_path, operation):
    config = InferenceConfig.model_validate({
        "generation_model": "anthropic:example",
        "generation_headers": {"X-Api-Key": "synthetic-static-key"},
        f"{operation}_headers": {"Authorization": "Bearer synthetic-gateway-token"},
    })
    identity = prepare_configuration_identity(tmp_path, {"run": config}, environment={"ANTHROPIC_PROFILE": "first"})
    assert (
        prepare_configuration_identity(
            tmp_path, {"run": config}, previous=identity, environment={"ANTHROPIC_PROFILE": "second"}
        )
        == identity
    )
