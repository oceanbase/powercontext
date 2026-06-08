"""Platform-aware defaults for local PowerMem startup."""

from __future__ import annotations

import importlib.util
import os
import sys
from dataclasses import dataclass
from typing import Any, Dict


SQLITE_LIMITATIONS = [
    "Graph Store is not available",
    "sub_stores routing is not available",
    "Sparse vector search is not available",
    "SkillStore requires OceanBase or embedded SeekDB",
]

SQLITE_RECOMMENDATION = (
    "SQLite is suitable for local development and basic memory CRUD/search. "
    'For the full PowerMem capability stack, use embedded SeekDB on Linux with '
    'pip install "powermem[seekdb]", or configure DATABASE_PROVIDER=oceanbase '
    "with OCEANBASE_HOST for a remote OceanBase cluster."
)


@dataclass(frozen=True)
class StorageDefaultDecision:
    provider: str
    reason: str
    defaulted: bool
    embedded_seekdb_available: bool


def database_provider_explicitly_configured() -> bool:
    return bool(os.environ.get("DATABASE_PROVIDER"))


def oceanbase_remote_configured() -> bool:
    return bool(os.environ.get("OCEANBASE_HOST"))


def _module_available(module_name: str) -> bool:
    try:
        return importlib.util.find_spec(module_name) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


def embedded_seekdb_available() -> bool:
    """Return whether embedded SeekDB can be used on this platform.

    This is intentionally a light capability probe. It avoids constructing an
    ObVecClient so configuration loading never creates data files or starts the
    embedded engine.
    """
    if not sys.platform.startswith("linux"):
        return False
    return (
        _module_available("pyobvector")
        and _module_available("pyseekdb")
        and _module_available("pylibseekdb")
    )


def choose_default_database_provider() -> StorageDefaultDecision:
    configured_provider = os.environ.get("DATABASE_PROVIDER")
    if configured_provider:
        return StorageDefaultDecision(
            provider=configured_provider.lower(),
            reason="DATABASE_PROVIDER is explicitly configured",
            defaulted=False,
            embedded_seekdb_available=embedded_seekdb_available(),
        )

    if oceanbase_remote_configured():
        return StorageDefaultDecision(
            provider="oceanbase",
            reason="OCEANBASE_HOST is configured, using remote OceanBase",
            defaulted=True,
            embedded_seekdb_available=embedded_seekdb_available(),
        )

    seekdb_available = embedded_seekdb_available()
    if seekdb_available:
        return StorageDefaultDecision(
            provider="oceanbase",
            reason="Linux embedded SeekDB capability detected",
            defaulted=True,
            embedded_seekdb_available=True,
        )

    return StorageDefaultDecision(
        provider="sqlite",
        reason="embedded SeekDB is not available on this platform, using SQLite",
        defaulted=True,
        embedded_seekdb_available=False,
    )


def default_database_provider() -> str:
    return choose_default_database_provider().provider


def storage_capabilities(provider: str, defaulted: bool | None = None) -> Dict[str, Any]:
    normalized = (provider or "").lower()
    if defaulted is None:
        defaulted = not database_provider_explicitly_configured()

    if normalized == "sqlite":
        return {
            "provider": "sqlite",
            "defaulted": bool(defaulted),
            "full_stack_available": False,
            "limitations": list(SQLITE_LIMITATIONS),
            "recommendation": SQLITE_RECOMMENDATION,
        }

    return {
        "provider": normalized or None,
        "defaulted": bool(defaulted),
        "full_stack_available": normalized == "oceanbase",
        "limitations": [],
        "recommendation": None,
    }


def sqlite_capability_warning(provider: str, defaulted: bool | None = None) -> str | None:
    capabilities = storage_capabilities(provider, defaulted=defaulted)
    if capabilities.get("provider") != "sqlite":
        return None
    limitations = "; ".join(capabilities["limitations"])
    return f"{capabilities['recommendation']} Limitations in SQLite mode: {limitations}."


def embedded_seekdb_unavailable_message() -> str:
    return (
        "Embedded SeekDB is not available on this platform or installation. "
        f"platform={sys.platform}, pyobvector={_module_available('pyobvector')}, "
        f"pyseekdb={_module_available('pyseekdb')}, "
        f"pylibseekdb={_module_available('pylibseekdb')}. "
        'Use DATABASE_PROVIDER=sqlite for local basic storage, install "powermem[seekdb]" '
        "on Linux for embedded SeekDB, or set OCEANBASE_HOST for a remote OceanBase cluster."
    )
