"""Immutable runtime configuration for the evaluation console."""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Literal, Self, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from powercontext_eval.codex import (
    DEFAULT_CODEX_MODEL,
    DEFAULT_CODEX_TIMEOUT_SECONDS,
    MAX_CODEX_TIMEOUT_SECONDS,
    MIN_CODEX_TIMEOUT_SECONDS,
    is_safe_codex_model,
    is_safe_openai_base_url,
)

_SAFE_DOCKER_NETWORK = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
_SAFE_CONTAINER_ENV_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_PRIVATE_CONTAINER_ENV_NAMES = frozenset({"OPENAI_API_KEY", "OPENAI_BASE_URL"})
_PRIVATE_CONTAINER_ENV_PREFIXES = ("POWERCONTEXT_CLIENT_", "POWERCONTEXT_SERVER_")
MAX_TASK_PARALLELISM = 30
MAX_TOKENSFLOW_FINALIZER_TIMEOUT_SECONDS = 600
MAX_CODEX_CAPACITY_RETRY_MAX = 20
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
    codex_timeout_seconds: Annotated[int, Field(ge=MIN_CODEX_TIMEOUT_SECONDS, le=MAX_CODEX_TIMEOUT_SECONDS)] = (
        DEFAULT_CODEX_TIMEOUT_SECONDS
    )
    tokensflow_finalizer_timeout_seconds: Annotated[int, Field(ge=60, le=MAX_TOKENSFLOW_FINALIZER_TIMEOUT_SECONDS)] = (
        600
    )
    tokensflow_finalizer_poll_seconds: Annotated[float, Field(gt=0, le=60)] = 5.0
    codex_capacity_retry_max: Annotated[int, Field(ge=0, le=MAX_CODEX_CAPACITY_RETRY_MAX)] = 5
    filesystem_min_free_bytes: Annotated[int, Field(ge=1)] = DEFAULT_FILESYSTEM_MIN_FREE_BYTES
    filesystem_min_free_inodes: Annotated[int, Field(ge=1)] = DEFAULT_FILESYSTEM_MIN_FREE_INODES
    workspace_reclaim_interval_seconds: Annotated[float, Field(gt=0, le=3600)] = 10.0

    @field_validator("task_parallelism", mode="before")
    @classmethod
    def require_integer_task_parallelism(cls, value: object) -> object:
        if not isinstance(value, str) or not value.isascii() or not value.isdecimal():
            raise ValueError("Task parallelism must be an integer")
        return value


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
    tokensflow_binary: Path
    tokensflow_user_home: Path = Field(exclude=True, repr=False)
    tokensflow_egress_network: str = Field(repr=False)
    uv_binary: Path
    registry_binary: Path
    auth_json: Path = Field(exclude=True, repr=False)
    proxy_url: str = Field(exclude=True, repr=False)
    private_container_env: dict[str, str] = Field(default_factory=dict, exclude=True, repr=False)
    codex_auth_mode: Literal["chatgpt", "api"] = "chatgpt"
    codex_api_key: str | None = Field(default=None, exclude=True, repr=False)
    codex_openai_base_url: str | None = Field(default=None, exclude=True, repr=False)
    host: str = Field(default="127.0.0.1", min_length=1)
    port: Annotated[int, Field(ge=1, le=65535)] = 8080
    lease_seconds: Annotated[int, Field(ge=1, le=3600)] = 60
    poll_seconds: Annotated[float, Field(gt=0, le=30)] = 1.0
    usage_pause_percent: Annotated[int, Field(ge=1, le=100)] = 80
    usage_probe_seconds: Annotated[int, Field(ge=10, le=3600)] = 60
    usage_probe_timeout_seconds: Annotated[int, Field(ge=1, le=60)] = 15
    usage_snapshot_max_age_seconds: Annotated[int, Field(ge=10, le=7200)] = 120
    task_parallelism: Annotated[int, Field(ge=1, le=MAX_TASK_PARALLELISM)] = 1
    codex_timeout_seconds: Annotated[int, Field(ge=MIN_CODEX_TIMEOUT_SECONDS, le=MAX_CODEX_TIMEOUT_SECONDS)] = (
        DEFAULT_CODEX_TIMEOUT_SECONDS
    )
    tokensflow_finalizer_timeout_seconds: Annotated[int, Field(ge=60, le=MAX_TOKENSFLOW_FINALIZER_TIMEOUT_SECONDS)] = (
        600
    )
    tokensflow_finalizer_poll_seconds: Annotated[float, Field(gt=0, le=60)] = 5.0
    codex_capacity_retry_max: Annotated[int, Field(ge=0, le=MAX_CODEX_CAPACITY_RETRY_MAX)] = 5
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
        "tokensflow_binary",
        "tokensflow_user_home",
        "uv_binary",
        "registry_binary",
        "auth_json",
    )
    @classmethod
    def require_absolute_path(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("Runtime paths must be absolute")
        return value

    @field_validator("tokensflow_egress_network")
    @classmethod
    def require_safe_tokensflow_egress_network(cls, value: str) -> str:
        if _SAFE_DOCKER_NETWORK.fullmatch(value) is None:
            raise ValueError("TokensFlow egress network is unsafe")
        return value

    @field_validator("private_container_env")
    @classmethod
    def require_safe_private_container_env(cls, value: dict[str, str]) -> dict[str, str]:
        if any(
            not _is_private_container_env_name(key)
            or _SAFE_CONTAINER_ENV_KEY.fullmatch(key) is None
            or "\n" in item
            or "\r" in item
            for key, item in value.items()
        ):
            raise ValueError("Private container environment contains an unsafe entry")
        return dict(value)

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
        if self.codex_auth_mode == "api":
            api_key = self.codex_api_key
            base_url = self.codex_openai_base_url
            if not api_key or any(character in api_key for character in "\0\r\n"):
                raise ValueError("API-key mode requires a safe Codex API key")
            if base_url is None or not is_safe_openai_base_url(base_url):
                raise ValueError("API-key mode requires a safe Codex OpenAI base URL")
        return self

    @classmethod
    def for_root(
        cls,
        root: Path,
        *,
        tokensflow_egress_network: str,
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
        proxy_url: str = "http://127.0.0.1:7890",
        private_container_env: Mapping[str, str] | None = None,
        codex_auth_mode: Literal["chatgpt", "api"] = "chatgpt",
        codex_api_key: str | None = None,
        codex_openai_base_url: str | None = None,
        host: str = "127.0.0.1",
        port: int = 8080,
        lease_seconds: int = 60,
        poll_seconds: float = 1.0,
        usage_pause_percent: int = 80,
        usage_probe_seconds: int = 60,
        usage_probe_timeout_seconds: int = 15,
        usage_snapshot_max_age_seconds: int = 120,
        task_parallelism: int = 1,
        codex_timeout_seconds: int = DEFAULT_CODEX_TIMEOUT_SECONDS,
        tokensflow_finalizer_timeout_seconds: int = 600,
        tokensflow_finalizer_poll_seconds: float = 5.0,
        codex_capacity_retry_max: int = 5,
        filesystem_min_free_bytes: int = DEFAULT_FILESYSTEM_MIN_FREE_BYTES,
        filesystem_min_free_inodes: int = DEFAULT_FILESYSTEM_MIN_FREE_INODES,
        workspace_reclaim_interval_seconds: float = 10.0,
        codex_models: tuple[str, ...] = (DEFAULT_CODEX_MODEL,),
    ) -> Self:
        effective_private_env = {} if private_container_env is None else dict(private_container_env)
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
            tokensflow_binary=tokensflow_binary or root / "bin" / "tokensflow",
            tokensflow_user_home=tokensflow_user_home or root / "tokensflow-home",
            tokensflow_egress_network=tokensflow_egress_network,
            uv_binary=uv_binary or root / "bin" / "uv",
            registry_binary=registry_binary or root / "bin" / "regctl",
            auth_json=auth_json or root / "codex-home" / "auth.json",
            proxy_url=proxy_url,
            private_container_env=effective_private_env,
            codex_auth_mode=codex_auth_mode,
            codex_api_key=codex_api_key,
            codex_openai_base_url=codex_openai_base_url,
            host=host,
            port=port,
            lease_seconds=lease_seconds,
            poll_seconds=poll_seconds,
            usage_pause_percent=usage_pause_percent,
            usage_probe_seconds=usage_probe_seconds,
            usage_probe_timeout_seconds=usage_probe_timeout_seconds,
            usage_snapshot_max_age_seconds=usage_snapshot_max_age_seconds,
            task_parallelism=task_parallelism,
            codex_timeout_seconds=codex_timeout_seconds,
            tokensflow_finalizer_timeout_seconds=tokensflow_finalizer_timeout_seconds,
            tokensflow_finalizer_poll_seconds=tokensflow_finalizer_poll_seconds,
            codex_capacity_retry_max=codex_capacity_retry_max,
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
                "codex_timeout_seconds": environ.get(
                    f"{prefix}CODEX_TIMEOUT_SECONDS", str(DEFAULT_CODEX_TIMEOUT_SECONDS)
                ),
                "tokensflow_finalizer_timeout_seconds": environ.get(
                    f"{prefix}TOKENSFLOW_FINALIZER_TIMEOUT_SECONDS", "600"
                ),
                "tokensflow_finalizer_poll_seconds": environ.get(f"{prefix}TOKENSFLOW_FINALIZER_POLL_SECONDS", "5"),
                "codex_capacity_retry_max": environ.get(f"{prefix}CODEX_CAPACITY_RETRY_MAX", "5"),
                "filesystem_min_free_bytes": environ.get(
                    f"{prefix}FILESYSTEM_MIN_FREE_BYTES", str(DEFAULT_FILESYSTEM_MIN_FREE_BYTES)
                ),
                "filesystem_min_free_inodes": environ.get(
                    f"{prefix}FILESYSTEM_MIN_FREE_INODES", str(DEFAULT_FILESYSTEM_MIN_FREE_INODES)
                ),
                "workspace_reclaim_interval_seconds": environ.get(f"{prefix}WORKSPACE_RECLAIM_INTERVAL_SECONDS", "10"),
            }
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
            tokensflow_binary=path("TOKENSFLOW_BINARY"),
            tokensflow_user_home=path("TOKENSFLOW_USER_HOME"),
            tokensflow_egress_network=environ[f"{prefix}TOKENSFLOW_EGRESS_NETWORK"],
            uv_binary=path("UV_BINARY"),
            registry_binary=path("REGISTRY_BINARY"),
            auth_json=path("AUTH_JSON"),
            proxy_url=environ.get(f"{prefix}PROXY_URL", "http://127.0.0.1:7890"),
            private_container_env={key: environ[key] for key in sorted(environ) if _is_private_container_env_name(key)},
            codex_auth_mode=cast(Literal["chatgpt", "api"], environ.get(f"{prefix}CODEX_AUTH_MODE", "chatgpt")),
            codex_api_key=environ.get(f"{prefix}CODEX_API_KEY"),
            codex_openai_base_url=environ.get(f"{prefix}CODEX_OPENAI_BASE_URL"),
            host=environ.get(f"{prefix}HOST", "127.0.0.1"),
            port=numbers.port,
            lease_seconds=numbers.lease_seconds,
            poll_seconds=numbers.poll_seconds,
            usage_pause_percent=numbers.usage_pause_percent,
            usage_probe_seconds=numbers.usage_probe_seconds,
            usage_probe_timeout_seconds=numbers.usage_probe_timeout_seconds,
            usage_snapshot_max_age_seconds=numbers.usage_snapshot_max_age_seconds,
            task_parallelism=numbers.task_parallelism,
            codex_timeout_seconds=numbers.codex_timeout_seconds,
            tokensflow_finalizer_timeout_seconds=numbers.tokensflow_finalizer_timeout_seconds,
            tokensflow_finalizer_poll_seconds=numbers.tokensflow_finalizer_poll_seconds,
            codex_capacity_retry_max=numbers.codex_capacity_retry_max,
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


def _is_private_container_env_name(name: str) -> bool:
    return name in _PRIVATE_CONTAINER_ENV_NAMES or name.startswith(_PRIVATE_CONTAINER_ENV_PREFIXES)
