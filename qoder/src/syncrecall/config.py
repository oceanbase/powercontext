"""Runtime configuration, read only from the environment.

Every value has a working default for a local single-machine deployment, so the
project starts after ``docker run`` + ``powercontext server run`` without any
editing. Nothing here contains a real credential: the seekdb container that the
README starts uses an empty root password, and the PowerContext Server runs with
access control disabled on loopback.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env(name: str, default: str) -> str:
    value = os.environ.get(name, "").strip()
    return value or default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    return int(raw)


@dataclass(frozen=True)
class Settings:
    """All knobs the application reads."""

    # --- seekdb (knowledge base + progress store) -------------------------------
    db_host: str = field(default_factory=lambda: _env("SR_DB_HOST", "127.0.0.1"))
    db_port: int = field(default_factory=lambda: _env_int("SR_DB_PORT", 2881))
    db_user: str = field(default_factory=lambda: _env("SR_DB_USER", "root"))
    db_password: str = field(default_factory=lambda: _env("SR_DB_PASSWORD", ""))
    db_name: str = field(default_factory=lambda: _env("SR_DB_NAME", "syncrecall"))

    # --- PowerContext Server (memory / context service) -------------------------
    powercontext_url: str = field(
        default_factory=lambda: _env("SR_POWERCONTEXT_URL", "http://127.0.0.1:18080")
    )

    # --- embedding model --------------------------------------------------------
    # No third-party API is called: the vectors come from a small local model.
    embedding_provider: str = field(
        default_factory=lambda: _env("SR_EMBEDDING_PROVIDER", "auto").lower()
    )
    embedding_model: str = field(
        default_factory=lambda: _env("SR_EMBEDDING_MODEL", "paraphrase-multilingual-MiniLM-L12-v2")
    )
    embedding_dim: int = field(default_factory=lambda: _env_int("SR_EMBEDDING_DIM", 384))

    # --- HTTP -------------------------------------------------------------------
    host: str = field(default_factory=lambda: _env("SR_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: _env_int("SR_PORT", 8788))

    @property
    def db_kwargs(self) -> dict:
        return {
            "host": self.db_host,
            "port": self.db_port,
            "user": self.db_user,
            "password": self.db_password,
            "charset": "utf8mb4",
        }


SETTINGS = Settings()
