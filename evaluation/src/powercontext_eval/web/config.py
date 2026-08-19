"""Immutable runtime configuration for the evaluation console."""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from powercontext_eval.codex import DEFAULT_CODEX_MODEL, is_safe_codex_model
from powercontext_eval.powercontext_sut import DEFAULT_DOCKER_NETWORK_POOL, ProxyRelayConfig, UnsafeSutConfiguration

_SAFE_DOCKER_NETWORK = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
_SAFE_NO_PROXY_HOST = re.compile(r"(?:[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?|\[[0-9A-Fa-f:]+\])")
MAX_TASK_PARALLELISM = 20
MAX_TOKENSFLOW_FINALIZER_TIMEOUT_SECONDS = 600
MAX_ATTEMPTS_LIMIT = 20
DEFAULT_MAX_ATTEMPTS = 5
DEFAULT_FILESYSTEM_MIN_FREE_BYTES = 10 * 1024**3
DEFAULT_FILESYSTEM_MIN_FREE_INODES = 1_000_000
FILESYSTEM_MIN_FREE_BYTES_PER_TASK = 4 * 1024**3
FILESYSTEM_MIN_FREE_INODES_PER_TASK = 250_000


class _EnvironmentNumbers(BaseModel):
    """Coerce textual environment values before strict runtime construction."""

    port: Annotated[int, Field(ge=1, le=65535)] = 8080
    lease_seconds: Annotated[int, Field(ge=1, le=3600)] = 60
    poll_seconds: Annotated[float, Field(gt=0, le=30)] = 1.0
    usage_pause_percent: Annotated[int, Field(ge=1, le=100)] = 80
    usage_probe_seconds: Annotated[int, Field(ge=10, le=3600)] = 60
    usage_probe_timeout_seconds: Annotated[int, Field(ge=1, le=60)] = 15
    usage_snapshot_max_age_seconds: Annotated[int, Field(ge=10, le=7200)] = 120
    task_parallelism: Annotated[int, Field(ge=1, le=MAX_TASK_PARALLELISM)] = 1
    tokensflow_finalizer_timeout_seconds: Annotated[int, Field(ge=60, le=MAX_TOKENSFLOW_FINALIZER_TIMEOUT_SECONDS)] = (
        600
    )
    tokensflow_finalizer_poll_seconds: Annotated[float, Field(gt=0, le=60)] = 5.0
    max_attempts: Annotated[int, Field(ge=1, le=MAX_ATTEMPTS_LIMIT)] = DEFAULT_MAX_ATTEMPTS
    filesystem_min_free_bytes: Annotated[int, Field(ge=1)] = DEFAULT_FILESYSTEM_MIN_FREE_BYTES
    filesystem_min_free_inodes: Annotated[int, Field(ge=1)] = DEFAULT_FILESYSTEM_MIN_FREE_INODES
    workspace_reclaim_interval_seconds: Annotated[float, Field(gt=0, le=3600)] = 10.0

    @field_validator("task_parallelism", mode="before")
    @classmethod
    def require_integer_task_parallelism(cls, value: object) -> object:
        if not isinstance(value, str) or not value.isascii() or not value.isdecimal():
            raise ValueError("Task parallelism must be an integer")
        return value


class _EnvironmentUsage(BaseModel):
    """Validate the accounting mode while preserving Pydantic configuration errors."""

    usage_mode: Literal["subscription", "api_key"] = "subscription"


class _EnvironmentFeatures(BaseModel):
    """Parse explicit opt-in feature switches from textual environment values."""

    tokensflow_enabled: bool = False


class WebConfig(BaseModel):
    """Validated process configuration with secret-bearing fields excluded from serialization."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    root: Path
    database_path: Path
    run_root: Path
    frontend_dist: Path
    powercontext_source: Path
    harness_root: Path
    harness_python: Path
    dataset_path: Path
    codex_binary: Path
    tokensflow_enabled: bool = False
    tokensflow_binary: Path | None = None
    tokensflow_user_home: Path | None = Field(default=None, exclude=True, repr=False)
    tokensflow_egress_network: str | None = Field(default=None, repr=False)
    uv_binary: Path
    registry_binary: Path
    auth_json: Path = Field(exclude=True, repr=False)
    codex_config: Path | None = Field(default=None, exclude=True, repr=False)
    proxy_url: str | None = Field(default=None, exclude=True, repr=False)
    docker_network_pool: str = DEFAULT_DOCKER_NETWORK_POOL
    extra_no_proxy_hosts: tuple[str, ...] = ()
    host: str = Field(default="127.0.0.1", min_length=1)
    port: Annotated[int, Field(ge=1, le=65535)] = 8080
    lease_seconds: Annotated[int, Field(ge=1, le=3600)] = 60
    poll_seconds: Annotated[float, Field(gt=0, le=30)] = 1.0
    usage_pause_percent: Annotated[int, Field(ge=1, le=100)] = 80
    usage_mode: Literal["subscription", "api_key"] = "subscription"
    usage_probe_seconds: Annotated[int, Field(ge=10, le=3600)] = 60
    usage_probe_timeout_seconds: Annotated[int, Field(ge=1, le=60)] = 15
    usage_snapshot_max_age_seconds: Annotated[int, Field(ge=10, le=7200)] = 120
    task_parallelism: Annotated[int, Field(ge=1, le=MAX_TASK_PARALLELISM)] = 1
    tokensflow_finalizer_timeout_seconds: Annotated[int, Field(ge=60, le=MAX_TOKENSFLOW_FINALIZER_TIMEOUT_SECONDS)] = (
        600
    )
    tokensflow_finalizer_poll_seconds: Annotated[float, Field(gt=0, le=60)] = 5.0
    max_attempts: Annotated[int, Field(ge=1, le=MAX_ATTEMPTS_LIMIT)] = DEFAULT_MAX_ATTEMPTS
    filesystem_min_free_bytes: Annotated[int, Field(ge=1)] = DEFAULT_FILESYSTEM_MIN_FREE_BYTES
    filesystem_min_free_inodes: Annotated[int, Field(ge=1)] = DEFAULT_FILESYSTEM_MIN_FREE_INODES
    workspace_reclaim_interval_seconds: Annotated[float, Field(gt=0, le=3600)] = 10.0
    codex_models: tuple[str, ...] = (DEFAULT_CODEX_MODEL,)

    @field_validator(
        "root",
        "database_path",
        "run_root",
        "frontend_dist",
        "powercontext_source",
        "harness_root",
        "harness_python",
        "dataset_path",
        "codex_binary",
        "uv_binary",
        "registry_binary",
        "auth_json",
    )
    @classmethod
    def require_absolute_path(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("Runtime paths must be absolute")
        return value

    @field_validator("tokensflow_binary", "tokensflow_user_home")
    @classmethod
    def require_optional_tokensflow_absolute_path(cls, value: Path | None) -> Path | None:
        if value is not None and not value.is_absolute():
            raise ValueError("Runtime paths must be absolute")
        return value

    @field_validator("codex_config")
    @classmethod
    def require_optional_absolute_path(cls, value: Path | None) -> Path | None:
        if value is not None and not value.is_absolute():
            raise ValueError("Runtime paths must be absolute")
        return value

    @field_validator("tokensflow_egress_network")
    @classmethod
    def require_safe_tokensflow_egress_network(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if _SAFE_DOCKER_NETWORK.fullmatch(value) is None:
            raise ValueError("TokensFlow egress network is unsafe")
        return value

    @field_validator("proxy_url")
    @classmethod
    def require_credential_free_loopback_proxy(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            ProxyRelayConfig(value)
        except UnsafeSutConfiguration as error:
            raise ValueError(str(error)) from error
        return value

    @field_validator("docker_network_pool")
    @classmethod
    def require_safe_docker_network_pool(cls, value: str) -> str:
        try:
            pool = ipaddress.ip_network(value, strict=True)
        except ValueError as error:
            raise ValueError("Docker network pool is invalid") from error
        if not isinstance(pool, ipaddress.IPv4Network) or not pool.is_private or pool.prefixlen > 23:
            raise ValueError("Docker network pool must be a private IPv4 network with at least 32 /28 subnets")
        return str(pool)

    @field_validator("extra_no_proxy_hosts")
    @classmethod
    def require_safe_extra_no_proxy_hosts(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        deduplicated = tuple(dict.fromkeys(value))
        if any(_SAFE_NO_PROXY_HOST.fullmatch(host) is None for host in deduplicated):
            raise ValueError("Additional no-proxy hosts are invalid")
        return deduplicated

    @field_validator("codex_models")
    @classmethod
    def require_safe_codex_models_with_default(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        deduplicated = tuple(dict.fromkeys(value))
        if not deduplicated or any(not is_safe_codex_model(model) for model in deduplicated):
            raise ValueError("Codex model allowlist is unsafe")
        if DEFAULT_CODEX_MODEL not in deduplicated:
            raise ValueError("Codex model allowlist must include the default model")
        return deduplicated

    @model_validator(mode="after")
    def require_usage_snapshot_to_cover_probe_interval(self) -> Self:
        if self.usage_snapshot_max_age_seconds < self.usage_probe_seconds:
            raise ValueError("Usage snapshot max age must cover at least one probe interval")
        return self

    @model_validator(mode="after")
    def require_enabled_feature_inputs(self) -> Self:
        if self.tokensflow_enabled and any(
            value is None
            for value in (self.tokensflow_binary, self.tokensflow_user_home, self.tokensflow_egress_network)
        ):
            raise ValueError("TokensFlow requires a binary, user home, and egress network when enabled")
        if self.proxy_url is None and self.extra_no_proxy_hosts:
            raise ValueError("Additional no-proxy hosts require the evaluation proxy")
        return self

    @classmethod
    def for_root(
        cls,
        root: Path,
        *,
        tokensflow_enabled: bool = False,
        tokensflow_egress_network: str | None = None,
        proxy_url: str | None = None,
        database_path: Path | None = None,
        run_root: Path | None = None,
        frontend_dist: Path | None = None,
        powercontext_source: Path | None = None,
        harness_root: Path | None = None,
        harness_python: Path | None = None,
        dataset_path: Path | None = None,
        raw_sample_path: Path | None = None,
        codex_binary: Path | None = None,
        tokensflow_binary: Path | None = None,
        tokensflow_user_home: Path | None = None,
        uv_binary: Path | None = None,
        registry_binary: Path | None = None,
        auth_json: Path | None = None,
        codex_config: Path | None = None,
        docker_network_pool: str = DEFAULT_DOCKER_NETWORK_POOL,
        extra_no_proxy_hosts: tuple[str, ...] = (),
        host: str = "127.0.0.1",
        port: int = 8080,
        lease_seconds: int = 60,
        poll_seconds: float = 1.0,
        usage_pause_percent: int = 80,
        usage_mode: Literal["subscription", "api_key"] = "subscription",
        usage_probe_seconds: int = 60,
        usage_probe_timeout_seconds: int = 15,
        usage_snapshot_max_age_seconds: int = 120,
        task_parallelism: int = 1,
        tokensflow_finalizer_timeout_seconds: int = 600,
        tokensflow_finalizer_poll_seconds: float = 5.0,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        filesystem_min_free_bytes: int = DEFAULT_FILESYSTEM_MIN_FREE_BYTES,
        filesystem_min_free_inodes: int = DEFAULT_FILESYSTEM_MIN_FREE_INODES,
        workspace_reclaim_interval_seconds: float = 10.0,
        codex_models: tuple[str, ...] = (DEFAULT_CODEX_MODEL,),
    ) -> Self:
        return cls(
            root=root,
            database_path=database_path or root / "web" / "tasks.sqlite3",
            run_root=run_root or root,
            frontend_dist=frontend_dist or root / "deploy" / "powercontext" / "evaluation" / "web" / "dist",
            powercontext_source=powercontext_source or root / "source" / "powercontext.git",
            harness_root=harness_root or root / "cache" / "swebench-pro.git",
            harness_python=harness_python or root / "venvs" / "swebench-pro-ca10a60" / "bin" / "python",
            dataset_path=dataset_path
            or raw_sample_path
            or root / "cache" / "swebench-pro.git" / "helper_code" / "sweap_eval_full_v2.jsonl",
            codex_binary=codex_binary or root / "bin" / "codex",
            tokensflow_enabled=tokensflow_enabled,
            tokensflow_binary=tokensflow_binary or (root / "bin" / "tokensflow" if tokensflow_enabled else None),
            tokensflow_user_home=tokensflow_user_home or (root / "tokensflow-home" if tokensflow_enabled else None),
            tokensflow_egress_network=tokensflow_egress_network,
            uv_binary=uv_binary or root / "bin" / "uv",
            registry_binary=registry_binary or root / "bin" / "regctl",
            auth_json=auth_json or root / "codex-home" / "auth.json",
            codex_config=codex_config,
            proxy_url=proxy_url,
            docker_network_pool=docker_network_pool,
            extra_no_proxy_hosts=extra_no_proxy_hosts,
            host=host,
            port=port,
            lease_seconds=lease_seconds,
            poll_seconds=poll_seconds,
            usage_pause_percent=usage_pause_percent,
            usage_mode=usage_mode,
            usage_probe_seconds=usage_probe_seconds,
            usage_probe_timeout_seconds=usage_probe_timeout_seconds,
            usage_snapshot_max_age_seconds=usage_snapshot_max_age_seconds,
            task_parallelism=task_parallelism,
            tokensflow_finalizer_timeout_seconds=tokensflow_finalizer_timeout_seconds,
            tokensflow_finalizer_poll_seconds=tokensflow_finalizer_poll_seconds,
            max_attempts=max_attempts,
            filesystem_min_free_bytes=filesystem_min_free_bytes,
            filesystem_min_free_inodes=filesystem_min_free_inodes,
            workspace_reclaim_interval_seconds=workspace_reclaim_interval_seconds,
            codex_models=codex_models,
        )

    @classmethod
    def from_environment(cls, environ: Mapping[str, str]) -> Self:
        prefix = "POWERCONTEXT_EVAL_"
        root = Path(environ[f"{prefix}ROOT"])

        def path(name: str) -> Path | None:
            value = environ.get(f"{prefix}{name}")
            return None if value is None else Path(value)

        numbers = _EnvironmentNumbers.model_validate(
            {
                "port": environ.get(f"{prefix}PORT", "8080"),
                "lease_seconds": environ.get(f"{prefix}LEASE_SECONDS", "60"),
                "poll_seconds": environ.get(f"{prefix}POLL_SECONDS", "1"),
                "usage_pause_percent": environ.get(f"{prefix}USAGE_PAUSE_PERCENT", "80"),
                "usage_probe_seconds": environ.get(f"{prefix}USAGE_PROBE_SECONDS", "60"),
                "usage_probe_timeout_seconds": environ.get(f"{prefix}USAGE_PROBE_TIMEOUT_SECONDS", "15"),
                "usage_snapshot_max_age_seconds": environ.get(f"{prefix}USAGE_SNAPSHOT_MAX_AGE_SECONDS", "120"),
                "task_parallelism": environ.get(f"{prefix}TASK_PARALLELISM", "1"),
                "tokensflow_finalizer_timeout_seconds": environ.get(
                    f"{prefix}TOKENSFLOW_FINALIZER_TIMEOUT_SECONDS", "600"
                ),
                "tokensflow_finalizer_poll_seconds": environ.get(f"{prefix}TOKENSFLOW_FINALIZER_POLL_SECONDS", "5"),
                "max_attempts": environ.get(f"{prefix}MAX_ATTEMPTS", str(DEFAULT_MAX_ATTEMPTS)),
                "filesystem_min_free_bytes": environ.get(
                    f"{prefix}FILESYSTEM_MIN_FREE_BYTES", str(DEFAULT_FILESYSTEM_MIN_FREE_BYTES)
                ),
                "filesystem_min_free_inodes": environ.get(
                    f"{prefix}FILESYSTEM_MIN_FREE_INODES", str(DEFAULT_FILESYSTEM_MIN_FREE_INODES)
                ),
                "workspace_reclaim_interval_seconds": environ.get(f"{prefix}WORKSPACE_RECLAIM_INTERVAL_SECONDS", "10"),
            }
        )
        usage = _EnvironmentUsage.model_validate({"usage_mode": environ.get(f"{prefix}USAGE_MODE", "subscription")})
        features = _EnvironmentFeatures.model_validate(
            {"tokensflow_enabled": environ.get(f"{prefix}TOKENSFLOW_ENABLED", "false")}
        )

        return cls.for_root(
            root,
            database_path=path("DATABASE_PATH"),
            run_root=path("RUN_ROOT"),
            frontend_dist=path("FRONTEND_DIST"),
            powercontext_source=path("POWERCONTEXT_SOURCE"),
            harness_root=path("HARNESS_ROOT"),
            harness_python=path("HARNESS_PYTHON"),
            dataset_path=path("DATASET_PATH") or path("RAW_SAMPLE_PATH"),
            codex_binary=path("CODEX_BINARY"),
            tokensflow_enabled=features.tokensflow_enabled,
            tokensflow_binary=path("TOKENSFLOW_BINARY") if features.tokensflow_enabled else None,
            tokensflow_user_home=path("TOKENSFLOW_USER_HOME") if features.tokensflow_enabled else None,
            tokensflow_egress_network=(
                environ.get(f"{prefix}TOKENSFLOW_EGRESS_NETWORK") if features.tokensflow_enabled else None
            ),
            proxy_url=environ.get(f"{prefix}PROXY_URL"),
            uv_binary=path("UV_BINARY"),
            registry_binary=path("REGISTRY_BINARY"),
            auth_json=path("AUTH_JSON"),
            codex_config=path("CODEX_CONFIG"),
            docker_network_pool=environ.get(f"{prefix}DOCKER_NETWORK_POOL", DEFAULT_DOCKER_NETWORK_POOL),
            extra_no_proxy_hosts=tuple(
                host for host in environ.get(f"{prefix}EXTRA_NO_PROXY_HOSTS", "").split(",") if host
            ),
            host=environ.get(f"{prefix}HOST", "127.0.0.1"),
            port=numbers.port,
            lease_seconds=numbers.lease_seconds,
            poll_seconds=numbers.poll_seconds,
            usage_pause_percent=numbers.usage_pause_percent,
            usage_mode=usage.usage_mode,
            usage_probe_seconds=numbers.usage_probe_seconds,
            usage_probe_timeout_seconds=numbers.usage_probe_timeout_seconds,
            usage_snapshot_max_age_seconds=numbers.usage_snapshot_max_age_seconds,
            task_parallelism=numbers.task_parallelism,
            tokensflow_finalizer_timeout_seconds=numbers.tokensflow_finalizer_timeout_seconds,
            tokensflow_finalizer_poll_seconds=numbers.tokensflow_finalizer_poll_seconds,
            max_attempts=numbers.max_attempts,
            filesystem_min_free_bytes=numbers.filesystem_min_free_bytes,
            filesystem_min_free_inodes=numbers.filesystem_min_free_inodes,
            workspace_reclaim_interval_seconds=numbers.workspace_reclaim_interval_seconds,
            codex_models=tuple(environ.get(f"{prefix}CODEX_MODELS", DEFAULT_CODEX_MODEL).split(",")),
        )

    @property
    def raw_sample_path(self) -> Path:
        """Compatibility alias while the runner migrates to catalog instances."""

        return self.dataset_path

    def accepts_codex_model(self, model: str) -> bool:
        """Apply the current admission policy to newly submitted work only."""

        return is_safe_codex_model(model) and model in self.codex_models

    @property
    def filesystem_claim_min_free_bytes(self) -> int:
        """Scale the byte reserve with the maximum number of concurrent task pairs."""

        return self.filesystem_min_free_bytes_for(self.task_parallelism)

    @property
    def filesystem_claim_min_free_inodes(self) -> int:
        """Scale the inode reserve with the maximum number of concurrent task pairs."""

        return self.filesystem_min_free_inodes_for(self.task_parallelism)

    def filesystem_min_free_bytes_for(self, task_parallelism: int) -> int:
        """Return the effective byte reserve for one published Worker capacity."""

        if not 1 <= task_parallelism <= MAX_TASK_PARALLELISM:
            raise ValueError("Task parallelism is outside the supported range")
        return max(self.filesystem_min_free_bytes, task_parallelism * FILESYSTEM_MIN_FREE_BYTES_PER_TASK)

    def filesystem_min_free_inodes_for(self, task_parallelism: int) -> int:
        """Return the effective inode reserve for one published Worker capacity."""

        if not 1 <= task_parallelism <= MAX_TASK_PARALLELISM:
            raise ValueError("Task parallelism is outside the supported range")
        return max(self.filesystem_min_free_inodes, task_parallelism * FILESYSTEM_MIN_FREE_INODES_PER_TASK)
