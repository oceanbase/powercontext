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

"""Subprocess entry for pinned Hermes code, with only local stub model calls."""

from __future__ import annotations

import importlib.util
import io
import json
import logging
import os
import platform
import sys
from pathlib import Path


class CheckoutLocalConfigError(ValueError):
    """The external checkout could load developer-local configuration."""

    def __init__(self):
        super().__init__("Hermes conformance requires a checkout without a local .env")


def reject_checkout_dotenv(checkout: Path) -> None:
    # Git's tracked-file check excludes .env, and Hermes reads it during imports.
    # lexists also rejects dangling links without opening the file or its target.
    if os.path.lexists(checkout / ".env"):
        raise CheckoutLocalConfigError


def main():
    directory, output, checkout = map(Path, sys.argv[1:])
    report = {
        "real-manager": {"status": "failed", "executed": False, "failure_category": "dependency_error"},
        "adapter-only": {"status": "unverified", "executed": False},
        "real-compaction": {"status": "unverified", "executed": False},
    }
    logs = io.StringIO()
    handler = logging.StreamHandler(logs)
    logging.getLogger().addHandler(handler)
    logging.getLogger().setLevel(logging.INFO)
    current = "real-manager"
    try:
        reject_checkout_dotenv(checkout)
        from agent.memory_manager import MemoryManager  # ty: ignore[unresolved-import]
        from agent.memory_provider import MemoryProvider  # ty: ignore[unresolved-import]

        from tests.integration_conformance.hermes.support import (
            assert_projected,
            exercise_scope_shutdown,
            exercise_storage,
        )

        root = Path(__file__).resolve().parents[3]
        plugin = root / "integrations" / "hermes" / "plugins" / "powercontext" / "__init__.py"
        spec = importlib.util.spec_from_file_location("pc_hermes_conformance", plugin)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        provider_type = module.PowerContextMemoryProvider
        assert issubclass(provider_type, MemoryProvider), "Provider must load the real Hermes base class"
        report["real-manager"].update(executed=True, failure_category="behavior_mismatch")
        # The parent sets the process's isolated HERMES_HOME before any host imports;
        # initialize_all binds that same temporary profile for the ownership check.
        report["real-manager"] = exercise_storage(provider_type, directory / "manager", MemoryManager)
        report["real-manager"]["python_version"] = platform.python_version()
        assert_projected(logs.getvalue())
        report["real-manager"]["operational_logs_clean"] = True
        current = "adapter-only"
        report[current].update(executed=True)
        report["adapter-only"] = exercise_scope_shutdown(provider_type, directory / "adapter")
        assert_projected(logs.getvalue())
        current = "real-compaction"
        report[current].update(status="failed", executed=True, failure_category="behavior_mismatch")
        from tests.integration_conformance.hermes.compaction import exercise_compaction

        report[current] = exercise_compaction(provider_type, directory / "compaction")
        assert_projected(logs.getvalue())
        return_code = 0
    except Exception as error:
        if isinstance(error, CheckoutLocalConfigError):
            category = "checkout_local_config"
        else:
            category = "dependency_error" if isinstance(error, ImportError) else "behavior_mismatch"
        report[current].update(status="failed", failure_category=category)
        return_code = 1
    finally:
        logging.getLogger().removeHandler(handler)
        handler.close()
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
