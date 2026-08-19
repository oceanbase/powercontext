"""Deployment contracts for the m0 evaluation-console services."""

from __future__ import annotations

import json
import re
from pathlib import Path

from powercontext_eval.web.config import WebConfig

EVALUATION = Path(__file__).resolve().parents[2]
DEPLOY = EVALUATION / "deploy"
EXPECTED_ENVIRONMENT_KEYS = {
    "POWERCONTEXT_EVAL_AUTH_JSON",
    "POWERCONTEXT_EVAL_CODEX_BINARY",
    "POWERCONTEXT_EVAL_CODEX_CAPACITY_RETRY_MAX",
    "POWERCONTEXT_EVAL_CODEX_MODELS",
    "POWERCONTEXT_EVAL_CODEX_TIMEOUT_SECONDS",
    "POWERCONTEXT_EVAL_DATABASE_PATH",
    "POWERCONTEXT_EVAL_DATASET_PATH",
    "POWERCONTEXT_EVAL_FRONTEND_DIST",
    "POWERCONTEXT_EVAL_HARNESS_PYTHON",
    "POWERCONTEXT_EVAL_HARNESS_ROOT",
    "POWERCONTEXT_EVAL_HOST",
    "POWERCONTEXT_EVAL_LEASE_SECONDS",
    "POWERCONTEXT_EVAL_POLL_SECONDS",
    "POWERCONTEXT_EVAL_PORT",
    "POWERCONTEXT_EVAL_POWERCONTEXT_SOURCE",
    "POWERCONTEXT_EVAL_PROXY_URL",
    "POWERCONTEXT_EVAL_REGISTRY_BINARY",
    "POWERCONTEXT_EVAL_ROOT",
    "POWERCONTEXT_EVAL_RUN_ROOT",
    "POWERCONTEXT_EVAL_TASK_PARALLELISM",
    "POWERCONTEXT_EVAL_TOKENSFLOW_BINARY",
    "POWERCONTEXT_EVAL_TOKENSFLOW_EGRESS_NETWORK",
    "POWERCONTEXT_EVAL_TOKENSFLOW_FINALIZER_POLL_SECONDS",
    "POWERCONTEXT_EVAL_TOKENSFLOW_FINALIZER_TIMEOUT_SECONDS",
    "POWERCONTEXT_EVAL_TOKENSFLOW_USER_HOME",
    "POWERCONTEXT_EVAL_USAGE_PAUSE_PERCENT",
    "POWERCONTEXT_EVAL_USAGE_PROBE_SECONDS",
    "POWERCONTEXT_EVAL_USAGE_PROBE_TIMEOUT_SECONDS",
    "POWERCONTEXT_EVAL_USAGE_SNAPSHOT_MAX_AGE_SECONDS",
    "POWERCONTEXT_EVAL_UV_BINARY",
}


def _unit(name: str) -> str:
    return (DEPLOY / name).read_text()


def test_systemd_units_run_the_pinned_checkout_with_role_appropriate_users() -> None:
    common = {
        "WorkingDirectory=/data/powercontext-eval/deploy/powercontext",
        "EnvironmentFile=/data/powercontext-eval/config/evaluation-console.env",
        "Restart=on-failure",
        "RestartSec=5s",
    }
    for command, name in (("web", "powercontext-eval-web.service"), ("worker", "powercontext-eval-worker.service")):
        unit = _unit(name)
        assert common <= set(unit.splitlines())
        assert (
            "ExecStart=/data/powercontext-eval/bin/uv run --project evaluation powercontext-eval " + command
        ) in unit
    web = set(_unit("powercontext-eval-web.service").splitlines())
    worker = set(_unit("powercontext-eval-worker.service").splitlines())
    assert {"User=rongfeng.frf", "Group=users"} <= web
    assert not {line for line in worker if line.startswith(("User=", "Group=", "SupplementaryGroups="))}


def test_systemd_units_keep_uv_cache_inside_the_writable_evaluation_root() -> None:
    for unit_name in ("powercontext-eval-web.service", "powercontext-eval-worker.service"):
        unit = (DEPLOY / unit_name).read_text()
        assert "Environment=UV_CACHE_DIR=/data/powercontext-eval/cache/uv" in unit.splitlines()


def test_systemd_units_deliver_one_graceful_term_and_only_web_accepts_uv_sigterm_status() -> None:
    web = set(_unit("powercontext-eval-web.service").splitlines())
    worker = set(_unit("powercontext-eval-worker.service").splitlines())

    assert "KillMode=mixed" in web
    assert "KillMode=mixed" in worker
    assert "SuccessExitStatus=143" in web
    assert not {line for line in worker if line.startswith("SuccessExitStatus=")}
    assert not {
        line for line in web | worker if line.startswith("SuccessExitStatus=") and "137" in line.split("=", 1)[1]
    }


def test_systemd_units_enforce_role_appropriate_security_boundaries() -> None:
    web = _unit("powercontext-eval-web.service")
    worker = _unit("powercontext-eval-worker.service")
    common = {
        "UMask=0077",
        "NoNewPrivileges=true",
        "PrivateTmp=true",
        "ProtectSystem=full",
        "ProtectHome=read-only",
        "ReadOnlyDirectories=/",
        "ReadWriteDirectories=/data/powercontext-eval",
    }
    assert common <= set(web.splitlines())
    assert not common & set(worker.splitlines())
    assert "/var/run/docker.sock" not in web
    assert "SupplementaryGroups=docker" not in worker
    assert "ReadWriteDirectories=/data/powercontext-eval" not in worker
    assert "After=network.target" in web
    assert "After=network.target powercontext-eval-web.service" in worker


def test_deployment_assets_do_not_manage_or_depend_on_existing_services() -> None:
    deployment = "\n".join(path.read_text() for path in DEPLOY.iterdir() if path.is_file())
    forbidden_services = {"new-api", "mysql", "redis", "proxy.service"}
    forbidden_commands = {
        "docker system prune",
        "docker rm",
        "systemctl restart new-api",
        "systemctl stop",
    }
    assert not any(name in deployment.lower() for name in forbidden_services)
    assert not any(command in deployment.lower() for command in forbidden_commands)


def test_example_environment_uses_only_supported_named_configuration() -> None:
    example = (DEPLOY / "powercontext-eval.env.example").read_text()
    keys = set(re.findall(r"^(POWERCONTEXT_EVAL_[A-Z_]+)=", example, re.MULTILINE))
    assert keys == EXPECTED_ENVIRONMENT_KEYS

    values = dict(re.findall(r"^(POWERCONTEXT_EVAL_[A-Z_]+)=(.*)$", example, re.MULTILINE))
    config = WebConfig.from_environment(values)
    assert config.root == Path("/data/powercontext-eval")
    assert config.host == "100.88.99.11"
    assert config.port == 8787
    assert config.proxy_url == "http://127.0.0.1:7890"
    assert config.powercontext_source == Path("/data/powercontext-eval/source/powercontext.git")
    assert config.dataset_path == Path(
        "/data/powercontext-eval/cache/swebench-pro.git/helper_code/sweap_eval_full_v2.jsonl"
    )
    assert config.usage_pause_percent == 80
    assert config.usage_probe_seconds == 60
    assert config.usage_probe_timeout_seconds == 15
    assert config.usage_snapshot_max_age_seconds == 120
    assert config.task_parallelism == 1
    assert config.codex_models == ("gpt-5.6-sol",)
    assert config.tokensflow_binary == Path("/usr/local/bin/tokensflow")
    assert config.tokensflow_user_home == Path("/home/evaluation-operator")
    assert config.tokensflow_binary.is_absolute()
    assert config.tokensflow_user_home.is_absolute()
    serialized = json.dumps(config.model_dump(mode="json"), sort_keys=True)
    assert "/home/evaluation-operator" not in serialized
    assert not re.search(r"(?i)(api[_-]?key|password|token|secret)=", example)


def test_frontend_build_uses_the_portable_rollup_runtime_required_by_m0() -> None:
    package = json.loads((EVALUATION / "web" / "package.json").read_text())
    lock = json.loads((EVALUATION / "web" / "package-lock.json").read_text())

    assert package["devDependencies"]["rollup"] == "npm:@rollup/wasm-node@4.62.3"
    assert lock["packages"]["node_modules/rollup"]["name"] == "@rollup/wasm-node"


def test_operator_guide_documents_safety_acceptance_and_rollback_contracts() -> None:
    guide = (EVALUATION / "README.md").read_text()
    required = {
        "chmod 0600",
        "systemd-analyze verify",
        "http://100.88.99.11:8787/api/health",
        "journalctl",
        "rollback",
        "m0",
        "unauthenticated",
        "Docker cleanup audit",
        "secret scan",
        "queue",
        "artifacts",
        '/data/powercontext-eval/bin/uv run --project evaluation pytest -c evaluation/pyproject.toml evaluation/tests -m "not live" -q',
        "/data/powercontext-eval/bin/uv run --directory evaluation ty check src tests",
        "evaluation/deploy/powercontext-eval.env.example",
        "install -d -o rongfeng.frf -g users -m 0700 /data/powercontext-eval/codex-home",
        "install -o rongfeng.frf -g users -m 0600",
        "sudo -u rongfeng.frf test -r /data/powercontext-eval/codex-home/auth.json",
        "operator-supplied",
        "The Worker runs as root",
        "731",
        "b5b2462bfbf5aeb2cb7ba7d215778a1768b85f9d7ad7f748546c7f80a0ad1510",
        "explicit final approval",
        "do not start",
        "sqlite3",
        "paired",
        "preview",
        "manual resume",
        "usage unavailable",
        "attempt",
        "boundary",
        "git push",
        "/data/powercontext-eval/source/powercontext.git",
        "node-v22.23.2-linux-x64-glibc-217",
        "npm ci",
        "npm run build",
    }
    assert all(term.lower() in guide.lower() for term in required)
    assert "tar -czf /tmp/powercontext-eval-frontend" not in guide


def test_operator_guide_documents_configurable_task_pair_parallelism() -> None:
    guide = (EVALUATION / "README.md").read_text()
    required = {
        "POWERCONTEXT_EVAL_TASK_PARALLELISM",
        "defaults to `1`",
        "thirty concurrent task pairs",
        "stop new claims",
        "active task pairs finish",
        "infrastructure failure",
        "active_task_pairs",
        "task_parallelism",
    }

    assert all(term.lower() in guide.lower() for term in required)
    assert "exactly one physical OFF/ON task pair running globally" not in guide


def test_operator_guide_documents_tokensflow_zero_loss_operation_and_recovery() -> None:
    guide = (EVALUATION / "README.md").read_text().lower()
    required = {
        "powercontext_eval_tokensflow_binary",
        "powercontext_eval_tokensflow_user_home",
        "configuration content replacement",
        "configured path switch",
        "loginctl enable-linger",
        "systemctl --user enable --now tokensflow.service",
        "tokensflow status",
        "whoami",
        "sha-256",
        "60-second",
        "upload --all",
        "duplicate",
        "tokensflow-recovery.json",
        "preserved private spool",
        "infrastructure failure",
        "single task",
        "manual resume",
        "rollback",
    }

    assert all(term in guide for term in required)


def test_operator_guide_stages_auth_without_printing_or_committing_it() -> None:
    guide = (EVALUATION / "README.md").read_text()
    assert "/data/powercontext-eval/config/auth.json.staged" in guide
    assert "unlink /data/powercontext-eval/config/auth.json.staged" in guide
    assert "stat -c '%U:%G %a'" in guide
    assert "cat " not in guide
    assert "auth.json=" not in guide
