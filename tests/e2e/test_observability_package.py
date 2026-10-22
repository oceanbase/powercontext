"""Opt-in source archive acceptance, including checkout-local secret exclusion."""

from __future__ import annotations

import os
import secrets
import shutil
import subprocess
import tarfile
from contextlib import suppress
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("PC_OBSERVABILITY_PACKAGE_ACCEPTANCE") != "1", reason="requires uv and build dependencies"
)


def test_distribution_ships_runtime_templates_and_excludes_local_state(tmp_path):
    root = Path(__file__).resolve().parents[2]
    suffix = secrets.token_hex(8)
    local = root / "deploy/observability/.local"
    created_directory = not local.exists()
    local.mkdir(parents=True, mode=0o700, exist_ok=True)
    paths = [local / f"package-sentinel-{suffix}.json", root / f".env.package-sentinel-{suffix}"]
    try:
        for path in paths:
            with path.open("x") as file:
                file.write("private packaging sentinel")
            path.chmod(0o600)
        executable = shutil.which("uv")
        assert executable is not None
        result = subprocess.run(
            [executable, "build", "--sdist", "--out-dir", str(tmp_path)],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        archive = next(tmp_path.glob("*.tar.gz"))
        with tarfile.open(archive) as package:
            names = package.getnames()
        assert not any("/.local/" in name or "/.env" in name for name in names)
        for target in (
            "observability/resources/stats-proxy.py",
            "observability/resources/compose.yaml",
            "observability/resources/grafana/dashboards/main/pc-overview.json",
            "observability/resources/grafana/dashboards/main/pc-providers.json",
            "observability/resources/grafana/dashboards/advanced/pc-business.json",
            "observability/resources/grafana/dashboards/advanced/pc-scopes.json",
        ):
            assert any(name.endswith(target) for name in names)
    finally:
        for path in paths:
            path.unlink(missing_ok=True)
        if created_directory:
            # Another process may now own state in this directory; remove only an empty directory.
            with suppress(OSError):
                local.rmdir()
