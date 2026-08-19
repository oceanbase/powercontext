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

"""Portable deployment contracts for the evaluation console."""

from __future__ import annotations

import json
import re
from pathlib import Path

from powercontext_eval.web.config import WebConfig

EVALUATION = Path(__file__).resolve().parents[2]
DEPLOY = EVALUATION / "deploy"
TEMPLATE_PLACEHOLDERS = {
    "@EVALUATION_ROOT@",
    "@REPOSITORY_ROOT@",
    "@UV_BINARY@",
    "@EVALUATION_USER@",
    "@EVALUATION_GROUP@",
    "@EVALUATION_WORKER_USER@",
    "@EVALUATION_WORKER_GROUP@",
}
EXPECTED_ENVIRONMENT_KEYS = {
    "POWERCONTEXT_EVAL_AUTH_JSON",
    "POWERCONTEXT_EVAL_CODEX_BINARY",
    "POWERCONTEXT_EVAL_CODEX_MODELS",
    "POWERCONTEXT_EVAL_DATABASE_PATH",
    "POWERCONTEXT_EVAL_DATASET_PATH",
    "POWERCONTEXT_EVAL_DOCKER_NETWORK_POOL",
    "POWERCONTEXT_EVAL_FRONTEND_DIST",
    "POWERCONTEXT_EVAL_HARNESS_PYTHON",
    "POWERCONTEXT_EVAL_HARNESS_ROOT",
    "POWERCONTEXT_EVAL_HOST",
    "POWERCONTEXT_EVAL_LEASE_SECONDS",
    "POWERCONTEXT_EVAL_MAX_ATTEMPTS",
    "POWERCONTEXT_EVAL_POLL_SECONDS",
    "POWERCONTEXT_EVAL_PORT",
    "POWERCONTEXT_EVAL_POWERCONTEXT_SOURCE",
    "POWERCONTEXT_EVAL_REGISTRY_BINARY",
    "POWERCONTEXT_EVAL_ROOT",
    "POWERCONTEXT_EVAL_RUN_ROOT",
    "POWERCONTEXT_EVAL_TASK_PARALLELISM",
    "POWERCONTEXT_EVAL_TOKENSFLOW_ENABLED",
    "POWERCONTEXT_EVAL_TOKENSFLOW_FINALIZER_POLL_SECONDS",
    "POWERCONTEXT_EVAL_TOKENSFLOW_FINALIZER_TIMEOUT_SECONDS",
    "POWERCONTEXT_EVAL_USAGE_MODE",
    "POWERCONTEXT_EVAL_USAGE_PAUSE_PERCENT",
    "POWERCONTEXT_EVAL_USAGE_PROBE_SECONDS",
    "POWERCONTEXT_EVAL_USAGE_PROBE_TIMEOUT_SECONDS",
    "POWERCONTEXT_EVAL_USAGE_SNAPSHOT_MAX_AGE_SECONDS",
    "POWERCONTEXT_EVAL_UV_BINARY",
}


def _template(name: str) -> str:
    return (DEPLOY / name).read_text()


def test_systemd_assets_are_unrendered_portable_templates() -> None:
    web = _template("powercontext-eval-web.service.in")
    worker = _template("powercontext-eval-worker.service.in")
    combined = web + worker

    assert {"@EVALUATION_ROOT@", "@REPOSITORY_ROOT@", "@UV_BINARY@"} <= {
        placeholder for placeholder in TEMPLATE_PLACEHOLDERS if placeholder in combined
    }
    assert {"@EVALUATION_USER@", "@EVALUATION_GROUP@"} <= set(re.findall(r"@[A-Z_]+@", web))
    assert {"@EVALUATION_WORKER_USER@", "@EVALUATION_WORKER_GROUP@"} <= set(re.findall(r"@[A-Z_]+@", worker))
    assert "KillMode=mixed" in web and "KillMode=mixed" in worker
    assert "Restart=on-failure" in web and "Restart=on-failure" in worker
    assert "ReadWriteDirectories=@EVALUATION_ROOT@" in web
    assert "docker.sock" not in web


def test_deployment_assets_require_rendered_identities_and_paths() -> None:
    deployment = "\n".join(path.read_text() for path in DEPLOY.iterdir() if path.is_file())
    assert not re.search(r"(?m)^(?:User|Group)=[^@]", deployment)
    assert not re.search(r"(?m)^(?:WorkingDirectory|EnvironmentFile|ExecStart)=/(?!srv/)", deployment)
    assert "docker system prune" not in deployment


def test_example_environment_is_complete_parseable_and_non_secret() -> None:
    example = (DEPLOY / "powercontext-eval.env.example").read_text()
    keys = set(re.findall(r"^(POWERCONTEXT_EVAL_[A-Z_]+)=", example, re.MULTILINE))
    assert keys == EXPECTED_ENVIRONMENT_KEYS

    values = dict(re.findall(r"^(POWERCONTEXT_EVAL_[A-Z_]+)=(.*)$", example, re.MULTILINE))
    config = WebConfig.from_environment(values)
    assert config.root == Path("/srv/powercontext-eval")
    assert config.host == "127.0.0.1"
    assert config.harness_python == Path("/srv/powercontext-eval/venvs/swebench-pro-ca10a60/bin/python")
    assert config.codex_config is None
    assert config.proxy_url is None
    assert config.tokensflow_enabled is False
    assert config.tokensflow_binary is None
    assert config.docker_network_pool == "172.30.0.0/15"
    assert config.extra_no_proxy_hosts == ()
    serialized = json.dumps(config.model_dump(mode="json"), sort_keys=True)
    assert "tokensflow-home" not in serialized
    assert not re.search(r"(?i)(api[_-]?key|password|token|secret)=", example)


def test_frontend_build_uses_portable_rollup_runtime() -> None:
    package = json.loads((EVALUATION / "web" / "package.json").read_text())
    lock = json.loads((EVALUATION / "web" / "package-lock.json").read_text())

    assert package["devDependencies"]["rollup"] == "npm:@rollup/wasm-node@4.62.3"
    assert lock["packages"]["node_modules/rollup"]["name"] == "@rollup/wasm-node"


def test_operator_guide_documents_portable_configuration_and_recovery() -> None:
    guide = (EVALUATION / "README.md").read_text().casefold()
    required = {
        "powercontext_eval_proxy_url",
        "powercontext_eval_tokensflow_enabled=true",
        "both are disabled by default",
        "powercontext_eval_docker_network_pool",
        "powercontext_eval_extra_no_proxy_hosts",
        "systemd-analyze verify",
        "only operator pause or cancel changes durable control intent",
        "task failures do not pause healthy peers",
        "startup-only orphan recovery",
        "never deletes running, queued, retryable, or finalizer-owned state",
        "do not use global docker prune",
        "rollback",
        "secret scan",
    }
    assert all(term in guide for term in required)
