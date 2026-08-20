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

from datetime import UTC, datetime
from math import inf, nan
from pathlib import Path

import pytest
from pydantic import ValidationError

from powercontext_eval.artifacts import ArmState
from powercontext_eval.runner import INSTANCE_ID
from powercontext_eval.web.batches import BatchCreate, PairCategory
from powercontext_eval.web.config import WebConfig
from powercontext_eval.web.controls import BatchPreviewRequest
from powercontext_eval.web.models import (
    ArmResponse,
    ComparisonResponse,
    EvidenceResponse,
    FailureCategory,
    FailureCode,
    ReportResponse,
    RetryDisposition,
    TaskCreate,
    TaskPhase,
    TaskRecord,
    TaskResult,
    TaskStatus,
    TreatmentEvidence,
)

PROXY_URL = "http://127.0.0.1:8081"


def valid_task(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "powercontext_ref": "latest",
        "benchmark": "swebench-pro",
        "instance_id": INSTANCE_ID,
        "model": "gpt-5.6-sol",
        "reasoning_effort": "medium",
        "treatment_mode": "off_on",
        "idempotency_key": "request-1234",
    }
    payload.update(overrides)
    return payload


def test_web_config_derives_confined_paths(tmp_path: Path) -> None:
    config = WebConfig.for_root(tmp_path)

    assert config.database_path == tmp_path / "web" / "tasks.sqlite3"
    assert config.run_root == tmp_path
    assert config.frontend_dist == tmp_path / "deploy" / "powercontext" / "evaluation" / "web" / "dist"
    assert config.tokensflow_enabled is False
    assert config.tokensflow_binary is None
    assert config.tokensflow_user_home is None
    assert config.tokensflow_egress_network is None
    assert config.proxy_url is None


def test_web_config_accepts_explicit_frontend_dist(tmp_path: Path) -> None:
    frontend_dist = tmp_path / "static"

    config = WebConfig.for_root(
        tmp_path, frontend_dist=frontend_dist, tokensflow_egress_network="bridge", proxy_url=PROXY_URL
    )

    assert config.frontend_dist == frontend_dist


def test_web_config_derives_portable_layout_from_any_root(tmp_path: Path) -> None:
    root = tmp_path / "evaluation-root"

    config = WebConfig.for_root(
        root,
        tokensflow_enabled=True,
        tokensflow_egress_network="bridge",
        proxy_url=PROXY_URL,
    )

    assert config.powercontext_source == root / "source" / "powercontext.git"
    assert config.harness_root == root / "cache" / "swebench-pro.git"
    assert config.harness_python == root / "venvs" / "swebench-pro-ca10a60" / "bin" / "python"
    assert config.dataset_path == root / "cache" / "swebench-pro.git" / "helper_code" / "sweap_eval_full_v2.jsonl"
    assert config.codex_binary == root / "bin" / "codex"
    assert config.tokensflow_binary == root / "bin" / "tokensflow"
    assert config.tokensflow_user_home == root / "tokensflow-home"
    assert config.tokensflow_egress_network == "bridge"
    assert config.uv_binary == root / "bin" / "uv"
    assert config.auth_json == root / "codex-home" / "auth.json"
    assert config.codex_config is None
    assert config.proxy_url == PROXY_URL
    assert config.docker_network_pool == "172.30.0.0/15"
    assert config.extra_no_proxy_hosts == ()
    assert config.frontend_dist == root / "deploy" / "powercontext" / "evaluation" / "web" / "dist"
    assert config.usage_pause_percent == 80
    assert config.usage_mode == "subscription"
    assert config.usage_probe_seconds == 60
    assert config.usage_probe_timeout_seconds == 15
    assert config.usage_snapshot_max_age_seconds == 120
    assert config.task_parallelism == 1
    assert config.tokensflow_finalizer_timeout_seconds == 600
    assert config.tokensflow_finalizer_poll_seconds == 5
    assert config.codex_models == ("gpt-5.6-sol",)


def test_web_config_parses_deduplicates_and_preserves_configured_codex_models(tmp_path: Path) -> None:
    config = WebConfig.from_environment(
        {
            "POWERCONTEXT_EVAL_ROOT": str(tmp_path),
            "POWERCONTEXT_EVAL_TOKENSFLOW_EGRESS_NETWORK": "bridge",
            "POWERCONTEXT_EVAL_PROXY_URL": PROXY_URL,
            "POWERCONTEXT_EVAL_CODEX_MODELS": "gpt-5.6-sol,gpt-5.6-luna,gpt-5.6-sol",
        }
    )

    assert config.codex_models == ("gpt-5.6-sol", "gpt-5.6-luna")


def test_web_config_accepts_api_key_usage_mode(tmp_path: Path) -> None:
    config = WebConfig.from_environment(
        {
            "POWERCONTEXT_EVAL_ROOT": str(tmp_path),
            "POWERCONTEXT_EVAL_TOKENSFLOW_EGRESS_NETWORK": "bridge",
            "POWERCONTEXT_EVAL_PROXY_URL": PROXY_URL,
            "POWERCONTEXT_EVAL_USAGE_MODE": "api_key",
        }
    )

    assert config.usage_mode == "api_key"


def test_web_config_rejects_unknown_usage_mode(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        WebConfig.from_environment(
            {
                "POWERCONTEXT_EVAL_ROOT": str(tmp_path),
                "POWERCONTEXT_EVAL_TOKENSFLOW_EGRESS_NETWORK": "bridge",
                "POWERCONTEXT_EVAL_PROXY_URL": PROXY_URL,
                "POWERCONTEXT_EVAL_USAGE_MODE": "unknown",
            }
        )


@pytest.mark.parametrize(
    "models",
    ["gpt-5.6-luna", "gpt-5.6-sol,unsafe model", "gpt-5.6-sol,,gpt-5.6-luna", ""],
)
def test_web_config_rejects_codex_allowlists_without_default_or_with_unsafe_entries(
    tmp_path: Path,
    models: str,
) -> None:
    with pytest.raises(ValidationError):
        WebConfig.from_environment(
            {
                "POWERCONTEXT_EVAL_ROOT": str(tmp_path),
                "POWERCONTEXT_EVAL_TOKENSFLOW_EGRESS_NETWORK": "bridge",
                "POWERCONTEXT_EVAL_PROXY_URL": PROXY_URL,
                "POWERCONTEXT_EVAL_CODEX_MODELS": models,
            }
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("root", Path("relative")),
        ("database_path", Path("relative.sqlite3")),
        ("port", 0),
        ("port", 65536),
        ("lease_seconds", 0),
        ("poll_seconds", 0.0),
        ("task_parallelism", 0),
        ("task_parallelism", 21),
        ("task_parallelism", "1"),
        ("tokensflow_finalizer_timeout_seconds", 601),
    ],
)
def test_web_config_direct_construction_rejects_invalid_values(tmp_path: Path, field: str, value: object) -> None:
    default = WebConfig.for_root(tmp_path, tokensflow_egress_network="bridge", proxy_url=PROXY_URL)
    payload = {name: getattr(default, name) for name in WebConfig.model_fields}

    with pytest.raises(ValidationError):
        WebConfig(**{**payload, field: value})  # ty: ignore[invalid-argument-type]


def test_web_config_from_environment_reads_only_named_variables(tmp_path: Path) -> None:
    frontend_dist = tmp_path / "frontend"
    environ = {
        "POWERCONTEXT_EVAL_ROOT": str(tmp_path),
        "POWERCONTEXT_EVAL_FRONTEND_DIST": str(frontend_dist),
        "POWERCONTEXT_EVAL_HOST": "127.0.0.2",
        "POWERCONTEXT_EVAL_PORT": "8123",
        "POWERCONTEXT_EVAL_LEASE_SECONDS": "90",
        "POWERCONTEXT_EVAL_POLL_SECONDS": "2.5",
        "POWERCONTEXT_EVAL_USAGE_PAUSE_PERCENT": "75",
        "POWERCONTEXT_EVAL_USAGE_PROBE_SECONDS": "90",
        "POWERCONTEXT_EVAL_USAGE_PROBE_TIMEOUT_SECONDS": "20",
        "POWERCONTEXT_EVAL_USAGE_SNAPSHOT_MAX_AGE_SECONDS": "180",
        "POWERCONTEXT_EVAL_TASK_PARALLELISM": "10",
        "POWERCONTEXT_EVAL_TOKENSFLOW_FINALIZER_TIMEOUT_SECONDS": "600",
        "POWERCONTEXT_EVAL_TOKENSFLOW_FINALIZER_POLL_SECONDS": "5",
        "POWERCONTEXT_EVAL_TOKENSFLOW_ENABLED": "true",
        "POWERCONTEXT_EVAL_TOKENSFLOW_BINARY": "/opt/tools/tokensflow",
        "POWERCONTEXT_EVAL_TOKENSFLOW_USER_HOME": "/srv/identities/current",
        "POWERCONTEXT_EVAL_TOKENSFLOW_EGRESS_NETWORK": "egress-net",
        "POWERCONTEXT_EVAL_PROXY_URL": PROXY_URL,
        "ROOT": "/ignored",
        "PORT": "1",
        "PROXY_URL": "https://ignored.invalid",
    }

    config = WebConfig.from_environment(environ)

    assert config.root == tmp_path
    assert config.frontend_dist == frontend_dist
    assert config.host == "127.0.0.2"
    assert config.port == 8123
    assert config.lease_seconds == 90
    assert config.poll_seconds == 2.5
    assert config.usage_pause_percent == 75
    assert config.usage_probe_seconds == 90
    assert config.usage_probe_timeout_seconds == 20
    assert config.usage_snapshot_max_age_seconds == 180
    assert config.task_parallelism == 10
    assert config.tokensflow_finalizer_timeout_seconds == 600
    assert config.tokensflow_finalizer_poll_seconds == 5
    assert config.tokensflow_binary == Path("/opt/tools/tokensflow")
    assert config.tokensflow_user_home == Path("/srv/identities/current")
    assert config.tokensflow_egress_network == "egress-net"


def test_web_config_reads_portable_network_settings_and_deduplicates_hosts(tmp_path: Path) -> None:
    config = WebConfig.from_environment(
        {
            "POWERCONTEXT_EVAL_ROOT": str(tmp_path),
            "POWERCONTEXT_EVAL_TOKENSFLOW_EGRESS_NETWORK": "bridge",
            "POWERCONTEXT_EVAL_PROXY_URL": PROXY_URL,
            "POWERCONTEXT_EVAL_DOCKER_NETWORK_POOL": "10.72.0.0/20",
            "POWERCONTEXT_EVAL_EXTRA_NO_PROXY_HOSTS": "mirror.example.test,10.0.0.7,mirror.example.test",
        }
    )

    assert config.docker_network_pool == "10.72.0.0/20"
    assert config.extra_no_proxy_hosts == ("mirror.example.test", "10.0.0.7")


@pytest.mark.parametrize("pool", ["public.example", "8.8.8.0/24", "10.0.0.0/24", "fd00::/64"])
def test_web_config_rejects_unsafe_docker_network_pool(tmp_path: Path, pool: str) -> None:
    with pytest.raises(ValidationError, match="Docker network pool"):
        WebConfig.for_root(
            tmp_path,
            tokensflow_egress_network="bridge",
            proxy_url=PROXY_URL,
            docker_network_pool=pool,
        )


@pytest.mark.parametrize("hosts", [("https://example.test",), ("host:8080",), ("bad host",)])
def test_web_config_rejects_unsafe_additional_no_proxy_hosts(tmp_path: Path, hosts: tuple[str, ...]) -> None:
    with pytest.raises(ValidationError, match="no-proxy hosts"):
        WebConfig.for_root(
            tmp_path,
            tokensflow_egress_network="bridge",
            proxy_url=PROXY_URL,
            extra_no_proxy_hosts=hosts,
        )


def test_web_config_requires_tokensflow_inputs_only_when_explicitly_enabled(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="TokensFlow requires"):
        WebConfig.from_environment(
            {
                "POWERCONTEXT_EVAL_ROOT": str(tmp_path),
                "POWERCONTEXT_EVAL_TOKENSFLOW_ENABLED": "true",
            }
        )


def test_web_config_minimal_environment_keeps_optional_integrations_disabled(tmp_path: Path) -> None:
    config = WebConfig.from_environment({"POWERCONTEXT_EVAL_ROOT": str(tmp_path)})

    assert config.tokensflow_enabled is False
    assert config.tokensflow_binary is None
    assert config.tokensflow_user_home is None
    assert config.tokensflow_egress_network is None
    assert config.proxy_url is None


def test_tokensflow_paths_do_not_implicitly_enable_internal_telemetry(tmp_path: Path) -> None:
    config = WebConfig.from_environment(
        {
            "POWERCONTEXT_EVAL_ROOT": str(tmp_path),
            "POWERCONTEXT_EVAL_TOKENSFLOW_BINARY": "/opt/tools/tokensflow",
            "POWERCONTEXT_EVAL_TOKENSFLOW_USER_HOME": "/srv/tokensflow-home",
            "POWERCONTEXT_EVAL_TOKENSFLOW_EGRESS_NETWORK": "bridge",
        }
    )

    assert config.tokensflow_enabled is False
    assert config.tokensflow_binary is None
    assert config.tokensflow_user_home is None
    assert config.tokensflow_egress_network is None


@pytest.mark.parametrize(
    "proxy_url",
    ["http://proxy.example.test:8080", "http://user:password@127.0.0.1:8081", "not-a-url"],
)
def test_web_config_rejects_non_loopback_or_credential_bearing_proxy(tmp_path: Path, proxy_url: str) -> None:
    with pytest.raises(ValidationError, match="Proxy upstream"):
        WebConfig.for_root(
            tmp_path,
            tokensflow_egress_network="bridge",
            proxy_url=proxy_url,
        )


@pytest.mark.parametrize("network", ["", "-bridge", "bridge other", "bridge;rm", 'bridge"bad', "a" * 129])
def test_web_config_rejects_unsafe_tokensflow_egress_network(tmp_path: Path, network: str) -> None:
    with pytest.raises(ValidationError, match="egress network"):
        WebConfig.for_root(tmp_path, tokensflow_egress_network=network, proxy_url=PROXY_URL)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("POWERCONTEXT_EVAL_ROOT", "relative"),
        ("POWERCONTEXT_EVAL_DATABASE_PATH", "relative.sqlite3"),
        ("POWERCONTEXT_EVAL_RUN_ROOT", "runs"),
        ("POWERCONTEXT_EVAL_FRONTEND_DIST", "dist"),
        ("POWERCONTEXT_EVAL_POWERCONTEXT_SOURCE", "source"),
        ("POWERCONTEXT_EVAL_HARNESS_ROOT", "harness"),
        ("POWERCONTEXT_EVAL_HARNESS_PYTHON", "python"),
        ("POWERCONTEXT_EVAL_DATASET_PATH", "sample.jsonl"),
        ("POWERCONTEXT_EVAL_CODEX_BINARY", "codex"),
        ("POWERCONTEXT_EVAL_TOKENSFLOW_BINARY", "tokensflow"),
        ("POWERCONTEXT_EVAL_TOKENSFLOW_USER_HOME", "profile"),
        ("POWERCONTEXT_EVAL_UV_BINARY", "uv"),
        ("POWERCONTEXT_EVAL_REGISTRY_BINARY", "regctl"),
        ("POWERCONTEXT_EVAL_AUTH_JSON", "auth.json"),
        ("POWERCONTEXT_EVAL_CODEX_CONFIG", "config.toml"),
    ],
)
def test_web_config_rejects_relative_paths(tmp_path: Path, name: str, value: str) -> None:
    environ = {
        "POWERCONTEXT_EVAL_ROOT": str(tmp_path),
        "POWERCONTEXT_EVAL_TOKENSFLOW_EGRESS_NETWORK": "bridge",
        "POWERCONTEXT_EVAL_PROXY_URL": PROXY_URL,
        name: value,
    }
    if name.startswith("POWERCONTEXT_EVAL_TOKENSFLOW_"):
        environ["POWERCONTEXT_EVAL_TOKENSFLOW_ENABLED"] = "true"

    with pytest.raises(ValidationError):
        WebConfig.from_environment(environ)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("POWERCONTEXT_EVAL_PORT", "0"),
        ("POWERCONTEXT_EVAL_PORT", "65536"),
        ("POWERCONTEXT_EVAL_LEASE_SECONDS", "0"),
        ("POWERCONTEXT_EVAL_POLL_SECONDS", "0"),
        ("POWERCONTEXT_EVAL_POLL_SECONDS", "31"),
        ("POWERCONTEXT_EVAL_USAGE_PAUSE_PERCENT", "0"),
        ("POWERCONTEXT_EVAL_USAGE_PAUSE_PERCENT", "101"),
        ("POWERCONTEXT_EVAL_USAGE_PROBE_SECONDS", "9"),
        ("POWERCONTEXT_EVAL_USAGE_PROBE_SECONDS", "3601"),
        ("POWERCONTEXT_EVAL_USAGE_PROBE_TIMEOUT_SECONDS", "0"),
        ("POWERCONTEXT_EVAL_USAGE_PROBE_TIMEOUT_SECONDS", "61"),
        ("POWERCONTEXT_EVAL_USAGE_SNAPSHOT_MAX_AGE_SECONDS", "9"),
        ("POWERCONTEXT_EVAL_USAGE_SNAPSHOT_MAX_AGE_SECONDS", "7201"),
        ("POWERCONTEXT_EVAL_TASK_PARALLELISM", "0"),
        ("POWERCONTEXT_EVAL_TASK_PARALLELISM", "21"),
        ("POWERCONTEXT_EVAL_TOKENSFLOW_FINALIZER_TIMEOUT_SECONDS", "601"),
        ("POWERCONTEXT_EVAL_TOKENSFLOW_FINALIZER_TIMEOUT_SECONDS", "3600"),
    ],
)
def test_web_config_rejects_invalid_numeric_settings(tmp_path: Path, name: str, value: str) -> None:
    with pytest.raises(ValidationError):
        WebConfig.from_environment(
            {
                "POWERCONTEXT_EVAL_ROOT": str(tmp_path),
                "POWERCONTEXT_EVAL_TOKENSFLOW_EGRESS_NETWORK": "bridge",
                "POWERCONTEXT_EVAL_PROXY_URL": PROXY_URL,
                name: value,
            }
        )


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("POWERCONTEXT_EVAL_PORT", "not-a-port"),
        ("POWERCONTEXT_EVAL_LEASE_SECONDS", "never"),
        ("POWERCONTEXT_EVAL_POLL_SECONDS", "soon"),
        ("POWERCONTEXT_EVAL_USAGE_PAUSE_PERCENT", "many"),
        ("POWERCONTEXT_EVAL_USAGE_PROBE_SECONDS", "often"),
        ("POWERCONTEXT_EVAL_USAGE_PROBE_TIMEOUT_SECONDS", "later"),
        ("POWERCONTEXT_EVAL_USAGE_SNAPSHOT_MAX_AGE_SECONDS", "fresh"),
        ("POWERCONTEXT_EVAL_TASK_PARALLELISM", "many"),
        ("POWERCONTEXT_EVAL_TASK_PARALLELISM", "1.0"),
    ],
)
def test_web_config_rejects_malformed_numeric_environment_values(tmp_path: Path, name: str, value: str) -> None:
    with pytest.raises(ValidationError):
        WebConfig.from_environment(
            {
                "POWERCONTEXT_EVAL_ROOT": str(tmp_path),
                "POWERCONTEXT_EVAL_TOKENSFLOW_EGRESS_NETWORK": "bridge",
                "POWERCONTEXT_EVAL_PROXY_URL": PROXY_URL,
                name: value,
            }
        )


def test_web_config_requires_usage_snapshot_to_cover_probe_interval(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        WebConfig.from_environment(
            {
                "POWERCONTEXT_EVAL_ROOT": str(tmp_path),
                "POWERCONTEXT_EVAL_TOKENSFLOW_EGRESS_NETWORK": "bridge",
                "POWERCONTEXT_EVAL_PROXY_URL": PROXY_URL,
                "POWERCONTEXT_EVAL_USAGE_PROBE_SECONDS": "90",
                "POWERCONTEXT_EVAL_USAGE_SNAPSHOT_MAX_AGE_SECONDS": "60",
            }
        )


def test_web_config_has_no_public_serialization_that_leaks_secrets(tmp_path: Path) -> None:
    config = WebConfig.for_root(
        tmp_path,
        auth_json=tmp_path / "auth-secret.json",
        codex_config=tmp_path / "provider-secret.toml",
        tokensflow_user_home=tmp_path / "identity-profile",
        tokensflow_egress_network="bridge",
        proxy_url=PROXY_URL,
    )

    assert not hasattr(config, "to_public")
    assert "auth_json" not in config.model_dump()
    assert "codex_config" not in config.model_dump()
    assert "proxy_url" not in config.model_dump()
    assert "tokensflow_user_home" not in config.model_dump()
    assert PROXY_URL not in repr(config)
    assert "auth-secret.json" not in repr(config)
    assert "provider-secret.toml" not in repr(config)
    assert "identity-profile" not in repr(config)


@pytest.mark.parametrize(
    "powercontext_ref",
    ["latest", "commit:0123456789abcdef0123456789abcdef01234567", "commit:ABCDEF0123456789ABCDEF0123456789ABCDEF01"],
)
def test_task_create_accepts_latest_or_exact_commit(powercontext_ref: str) -> None:
    request = TaskCreate.model_validate(valid_task(powercontext_ref=powercontext_ref))

    assert request.powercontext_ref == powercontext_ref


@pytest.mark.parametrize(
    "powercontext_ref",
    ["branch:main", "tag:v1", "main", "commit:0123456", "commit:" + "g" * 40],
)
def test_task_create_rejects_unsupported_revision(powercontext_ref: str) -> None:
    with pytest.raises(ValidationError):
        TaskCreate.model_validate(valid_task(powercontext_ref=powercontext_ref))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("benchmark", "swebench"),
        ("instance_id", "unsafe/instance"),
        ("model", "unsafe model"),
        ("reasoning_effort", "high"),
        ("treatment_mode", "on"),
        ("idempotency_key", "unsafe key"),
        ("idempotency_key", "short"),
    ],
)
def test_task_create_rejects_values_outside_capabilities(field: str, value: str) -> None:
    with pytest.raises(ValidationError):
        TaskCreate.model_validate(valid_task(**{field: value}))


def test_task_create_accepts_catalog_instance_ids() -> None:
    request = TaskCreate.model_validate(valid_task(instance_id="instance_owner__repo-b"))

    assert request.instance_id == "instance_owner__repo-b"


def test_batch_create_pins_the_public_v2_task_set() -> None:
    request = BatchCreate(
        powercontext_ref="latest",
        benchmark="swebench-pro",
        task_set="swebench-pro-public-v2",
        model="gpt-5.6-sol",
        reasoning_effort="medium",
        treatment_mode="off_on",
        idempotency_key="batch-request",
    )

    assert request.task_set == "swebench-pro-public-v2"
    assert request.usage_pause_percent == 80
    assert [category.value for category in PairCategory] == [
        "off_fail_on_pass",
        "off_pass_on_fail",
        "both_pass",
        "both_fail",
        "execution_failure",
    ]


def test_batch_create_accepts_the_pinned_stability_task_set() -> None:
    request = BatchCreate(
        powercontext_ref="latest",
        benchmark="swebench-pro",
        task_set="swebench-pro-stability-v1",
        treatment_mode="off_on",
        idempotency_key="stability-request",
    )

    assert request.task_set == "swebench-pro-stability-v1"


def test_batch_model_defaults_to_sol_and_accepts_luna_without_an_allowlist() -> None:
    base = {
        "powercontext_ref": "latest",
        "benchmark": "swebench-pro",
        "task_set": "swebench-pro-public-v2",
        "treatment_mode": "off_on",
        "idempotency_key": "batch-request",
    }

    default = BatchCreate.model_validate(base)
    luna = BatchCreate.model_validate({**base, "model": "gpt-5.6-luna"})
    preview = BatchPreviewRequest(powercontext_ref="latest", model="gpt-5.6-luna")
    task = TaskCreate.model_validate(valid_task(model="gpt-5.6-luna", idempotency_key="luna-task-request"))

    assert (default.model, default.reasoning_effort) == ("gpt-5.6-sol", "medium")
    assert (luna.model, luna.reasoning_effort) == ("gpt-5.6-luna", "medium")
    assert preview.model == task.model == "gpt-5.6-luna"


def test_batch_initial_control_defaults_to_run_and_accepts_pause() -> None:
    base = {
        "powercontext_ref": "latest",
        "benchmark": "swebench-pro",
        "task_set": "swebench-pro-public-v2",
        "treatment_mode": "off_on",
        "idempotency_key": "initial-control",
    }

    assert BatchCreate.model_validate(base).initial_control_intent == "run"
    assert BatchCreate.model_validate({**base, "initial_control_intent": "pause"}).initial_control_intent == "pause"
    with pytest.raises(ValidationError):
        BatchCreate.model_validate({**base, "initial_control_intent": "cancel"})


@pytest.mark.parametrize(
    "model",
    ["", "-c", "gpt-5.6-luna --disable plugins", "gpt-5.6-luna\n-c", "gpt/../../model", "模型"],
)
def test_batch_and_task_reject_unsafe_model_names(model: str) -> None:
    with pytest.raises(ValidationError):
        BatchCreate(
            powercontext_ref="latest",
            benchmark="swebench-pro",
            task_set="swebench-pro-public-v2",
            model=model,
            treatment_mode="off_on",
            idempotency_key="unsafe-model",
        )
    with pytest.raises(ValidationError):
        TaskCreate.model_validate(valid_task(model=model))


def test_batch_create_accepts_a_per_batch_usage_threshold_override() -> None:
    request = BatchCreate(
        powercontext_ref="latest",
        benchmark="swebench-pro",
        task_set="swebench-pro-public-v2",
        model="gpt-5.6-sol",
        reasoning_effort="medium",
        treatment_mode="off_on",
        idempotency_key="batch-request",
        usage_pause_percent=75,
    )

    assert request.usage_pause_percent == 75


@pytest.mark.parametrize("value", [0, 101, True, 80.5, "80"])
def test_batch_create_strictly_rejects_invalid_usage_threshold(value: object) -> None:
    payload = {
        "powercontext_ref": "latest",
        "benchmark": "swebench-pro",
        "task_set": "swebench-pro-public-v2",
        "model": "gpt-5.6-sol",
        "reasoning_effort": "medium",
        "treatment_mode": "off_on",
        "idempotency_key": "batch-request",
        "usage_pause_percent": value,
    }

    with pytest.raises(ValidationError):
        BatchCreate.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("powercontext_ref", "branch:main"),
        ("benchmark", "swebench"),
        ("task_set", "sample"),
        ("model", "gpt-5;rm"),
        ("reasoning_effort", "high"),
        ("treatment_mode", "on"),
        ("idempotency_key", "unsafe key"),
    ],
)
def test_batch_create_rejects_values_outside_fixed_batch_contract(field: str, value: str) -> None:
    payload = {
        "powercontext_ref": "latest",
        "benchmark": "swebench-pro",
        "task_set": "swebench-pro-public-v2",
        "model": "gpt-5.6-sol",
        "reasoning_effort": "medium",
        "treatment_mode": "off_on",
        "idempotency_key": "batch-request",
    }

    with pytest.raises(ValidationError):
        BatchCreate.model_validate({**payload, field: value})


def test_task_create_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        TaskCreate.model_validate(valid_task(unexpected="value"))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("powercontext_ref", b"latest"),
        ("benchmark", b"swebench-pro"),
        ("instance_id", 123),
        ("model", b"gpt-5.6-sol"),
        ("reasoning_effort", b"medium"),
        ("treatment_mode", b"off_on"),
        ("idempotency_key", b"request-1234"),
    ],
)
def test_task_create_strictly_rejects_coercible_non_strings(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        TaskCreate.model_validate(valid_task(**{field: value}))


@pytest.mark.parametrize(
    "idempotency_key",
    [
        "a" * 129,
        "request key",
        "request/key",
        "request:key",
        "request@key",
        "request+key",
        "request$key",
    ],
)
def test_task_create_rejects_oversized_or_unsafe_idempotency_keys(idempotency_key: str) -> None:
    with pytest.raises(ValidationError):
        TaskCreate.model_validate(valid_task(idempotency_key=idempotency_key))


def test_task_status_values_are_exact() -> None:
    assert [item.value for item in TaskStatus] == [
        "queued",
        "running",
        "succeeded",
        "failed",
        "interrupted",
        "cancelled",
    ]


def test_task_phase_values_are_exact() -> None:
    assert [item.value for item in TaskPhase] == [
        "preparing",
        "validating_gold",
        "running_off",
        "running_on",
        "official_evaluation",
        "generating_report",
    ]


def test_failure_category_values_are_exact() -> None:
    assert [item.value for item in FailureCategory] == [
        "invalid_request",
        "queue_unavailable",
        "source_resolution_failure",
        "environment_preparation_failure",
        "gold_validation_failure",
        "codex_execution_failure",
        "codex_capacity_failure",
        "treatment_validation_failure",
        "official_evaluator_failure",
        "report_generation_failure",
        "worker_interruption",
        "internal",
    ]


def test_task_record_exposes_only_safe_failure_details() -> None:
    record = TaskRecord(
        task_id="run-123",
        request=TaskCreate.model_validate(valid_task()),
        status=TaskStatus.FAILED,
        created_at=datetime(2026, 7, 29, tzinfo=UTC),
        eligible_at=datetime(2026, 7, 29, tzinfo=UTC),
        started_at=datetime(2026, 7, 29, tzinfo=UTC),
        finished_at=datetime(2026, 7, 29, 0, 1, tzinfo=UTC),
        failure_category=FailureCategory.CODEX_EXECUTION,
        failure_code=FailureCode.CODEX_EXECUTION,
        retry_disposition=RetryDisposition.RETRY,
        failure_phase=TaskPhase.RUNNING_OFF,
        failure_summary="Codex did not complete. Inspect retained worker logs.",
    )

    assert record.failure_category is FailureCategory.CODEX_EXECUTION
    assert record.failure_summary == "Codex did not complete. Inspect retained worker logs."


def record_payload(status: TaskStatus) -> dict[str, object]:
    return {
        "task_id": "run-123",
        "request": TaskCreate.model_validate(valid_task()),
        "status": status,
        "created_at": datetime(2026, 7, 29, tzinfo=UTC),
        "eligible_at": datetime(2026, 7, 29, tzinfo=UTC),
    }


@pytest.mark.parametrize(
    ("status", "fields"),
    [
        (TaskStatus.QUEUED, {"started_at": datetime(2026, 7, 29, tzinfo=UTC)}),
        (TaskStatus.RUNNING, {}),
        (
            TaskStatus.RUNNING,
            {"started_at": datetime(2026, 7, 29, tzinfo=UTC), "finished_at": datetime(2026, 7, 29, tzinfo=UTC)},
        ),
        (
            TaskStatus.SUCCEEDED,
            {"started_at": datetime(2026, 7, 29, tzinfo=UTC), "finished_at": datetime(2026, 7, 29, tzinfo=UTC)},
        ),
        (
            TaskStatus.FAILED,
            {
                "started_at": datetime(2026, 7, 29, tzinfo=UTC),
                "finished_at": datetime(2026, 7, 29, tzinfo=UTC),
                "failure_category": FailureCategory.CODEX_EXECUTION,
            },
        ),
        (
            TaskStatus.INTERRUPTED,
            {
                "started_at": datetime(2026, 7, 29, tzinfo=UTC),
                "finished_at": datetime(2026, 7, 29, tzinfo=UTC),
                "failure_category": FailureCategory.WORKER_INTERRUPTION,
                "failure_phase": TaskPhase.RUNNING_ON,
                "failure_summary": "Worker lease expired.",
                "result": {
                    "artifact_dir": "runs/run-123",
                    "report_path": "runs/run-123/report.md",
                    "off_resolved": False,
                    "on_resolved": False,
                },
            },
        ),
        (TaskStatus.CANCELLED, {}),
        (
            TaskStatus.CANCELLED,
            {"finished_at": datetime(2026, 7, 29, tzinfo=UTC), "started_at": datetime(2026, 7, 29, tzinfo=UTC)},
        ),
    ],
)
def test_task_record_rejects_incoherent_lifecycle(status: TaskStatus, fields: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        TaskRecord.model_validate({**record_payload(status), **fields})


@pytest.mark.parametrize(
    ("status", "fields"),
    [
        (TaskStatus.QUEUED, {}),
        (TaskStatus.RUNNING, {"started_at": datetime(2026, 7, 29, tzinfo=UTC)}),
        (
            TaskStatus.SUCCEEDED,
            {
                "started_at": datetime(2026, 7, 29, tzinfo=UTC),
                "finished_at": datetime(2026, 7, 29, tzinfo=UTC),
                "result": TaskResult(
                    artifact_dir="runs/run-123",
                    report_path="runs/run-123/report.md",
                    off_resolved=True,
                    on_resolved=True,
                ),
            },
        ),
        (
            TaskStatus.FAILED,
            {
                "started_at": datetime(2026, 7, 29, tzinfo=UTC),
                "finished_at": datetime(2026, 7, 29, tzinfo=UTC),
                "failure_category": FailureCategory.CODEX_EXECUTION,
                "failure_code": FailureCode.CODEX_EXECUTION,
                "retry_disposition": RetryDisposition.RETRY,
                "failure_phase": TaskPhase.RUNNING_OFF,
                "failure_summary": "Codex did not complete.",
            },
        ),
        (
            TaskStatus.INTERRUPTED,
            {
                "started_at": datetime(2026, 7, 29, tzinfo=UTC),
                "finished_at": datetime(2026, 7, 29, tzinfo=UTC),
                "failure_category": FailureCategory.WORKER_INTERRUPTION,
                "failure_code": FailureCode.WORKER_INTERRUPTION,
                "retry_disposition": RetryDisposition.RETRY,
                "failure_phase": TaskPhase.RUNNING_ON,
                "failure_summary": "Worker lease expired.",
            },
        ),
        (TaskStatus.CANCELLED, {"finished_at": datetime(2026, 7, 29, tzinfo=UTC)}),
    ],
)
def test_task_record_accepts_coherent_lifecycle(status: TaskStatus, fields: dict[str, object]) -> None:
    assert TaskRecord.model_validate({**record_payload(status), **fields}).status is status


def test_failed_task_accepts_safe_failure_without_phase() -> None:
    record = TaskRecord.model_validate(
        {
            **record_payload(TaskStatus.FAILED),
            "started_at": datetime(2026, 7, 29, tzinfo=UTC),
            "finished_at": datetime(2026, 7, 29, 0, 1, tzinfo=UTC),
            "failure_category": FailureCategory.INTERNAL,
            "failure_code": FailureCode.INTERNAL,
            "retry_disposition": RetryDisposition.RETRY,
            "failure_summary": "The worker failed unexpectedly.",
        }
    )

    assert record.failure_phase is None


@pytest.mark.parametrize(
    "failure",
    [
        {"failure_category": FailureCategory.INTERNAL},
        {"failure_summary": "The worker failed unexpectedly."},
    ],
)
def test_task_record_rejects_partial_failure_category_and_summary(failure: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        TaskRecord.model_validate(
            {
                **record_payload(TaskStatus.FAILED),
                "started_at": datetime(2026, 7, 29, tzinfo=UTC),
                "finished_at": datetime(2026, 7, 29, 0, 1, tzinfo=UTC),
                **failure,
            }
        )


@pytest.mark.parametrize(
    ("status", "fields"),
    [
        (
            TaskStatus.RUNNING,
            {"started_at": datetime(2026, 7, 28, 23, 59, tzinfo=UTC)},
        ),
        (
            TaskStatus.SUCCEEDED,
            {
                "started_at": datetime(2026, 7, 29, 0, 2, tzinfo=UTC),
                "finished_at": datetime(2026, 7, 29, 0, 1, tzinfo=UTC),
                "result": TaskResult(
                    artifact_dir="runs/run-123",
                    report_path="runs/run-123/report.md",
                    off_resolved=True,
                    on_resolved=True,
                ),
            },
        ),
        (
            TaskStatus.CANCELLED,
            {"finished_at": datetime(2026, 7, 28, 23, 59, tzinfo=UTC)},
        ),
    ],
)
def test_task_record_rejects_inverted_timestamp_order(status: TaskStatus, fields: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        TaskRecord.model_validate({**record_payload(status), **fields})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("input_tokens", -1),
        ("output_tokens", -1),
        ("elapsed_seconds", -0.1),
        ("patch_bytes", -1),
        ("elapsed_seconds", nan),
        ("elapsed_seconds", inf),
    ],
)
def test_report_arm_rejects_negative_or_non_finite_metrics(field: str, value: float) -> None:
    with pytest.raises(ValidationError):
        ArmResponse.model_validate(
            {
                "arm": "off",
                "state": ArmState.TREATMENT_VALIDATED,
                "resolution": "resolved",
                "passed": True,
                "treatment_valid": True,
                field: value,
            }
        )


def test_report_response_nested_mappings_are_immutable() -> None:
    evidence = TreatmentEvidence(
        mcp_requests=0,
        prompt_sources=0,
        plugin_checkout_sha="0" * 40,
        plugin_id="powercontext",
        plugin_installed=False,
        plugin_version="0.1.0",
        scope_id="scope",
        server_ready=False,
    )
    report = ReportResponse(
        task_id="run-123",
        acceptance_valid=False,
        off=ArmResponse(
            arm="off",
            state=ArmState.TREATMENT_VALIDATED,
            resolution="unresolved",
            passed=False,
            treatment_valid=True,
        ),
        on=ArmResponse(
            arm="on",
            state=ArmState.INVALID_TREATMENT,
            resolution="unresolved",
            passed=None,
            treatment_valid=False,
        ),
        comparison=ComparisonResponse(),
        evidence=EvidenceResponse(off=evidence, on=evidence),
        revisions={"powercontext": "0" * 40},
        configuration={"model": "gpt-5.6-sol"},
        generated_at=datetime(2026, 7, 29, tzinfo=UTC),
    )

    with pytest.raises(TypeError):
        report.revisions["powercontext"] = "1" * 40  # ty: ignore[invalid-assignment]
    with pytest.raises(TypeError):
        report.configuration["model"] = "other"  # ty: ignore[invalid-assignment]
    assert ReportResponse.model_validate_json(report.model_dump_json()).revisions["powercontext"] == "0" * 40
    assert report.model_dump(mode="json")["off"] == {
        "arm": "off",
        "state": "treatment_validated",
        "resolution": "unresolved",
        "passed": False,
        "treatment_valid": True,
        "input_tokens": None,
        "output_tokens": None,
        "elapsed_seconds": None,
        "patch_bytes": None,
    }
