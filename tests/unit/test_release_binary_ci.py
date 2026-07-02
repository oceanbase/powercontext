import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

from scripts.smoke_binary_package import _package_name

ROOT = Path(__file__).resolve().parents[2]


def test_binary_builder_verifies_miniconda_installer_checksum() -> None:
    dockerfile = (ROOT / "docker" / "Dockerfile.binaries-centos7").read_text()

    assert "ARG MINICONDA_SHA256=" in dockerfile
    assert (
        "634d76df5e489c44ade4085552b97bebc786d49245ed1a830022b0b406de5817" in dockerfile
    )
    assert (
        'echo "${MINICONDA_SHA256}  /tmp/miniconda.sh" | sha256sum -c -' in dockerfile
    )


def test_release_binary_help_smokes_are_bounded() -> None:
    smoke_script = (ROOT / "scripts" / "smoke_binary_package.py").read_text()

    assert '[str(binary), "--help"]' in smoke_script
    assert "timeout=60" in smoke_script
    assert "_run_checked(" in smoke_script
    assert '_run_help(binaries[f"powermem-mcp{exe_suffix}"])' in smoke_script


def test_release_binary_mcp_entrypoint_uses_lightweight_cli() -> None:
    builder = (ROOT / "scripts" / "build_binary_package.sh").read_text()

    assert "from powermem.mcp.cli import main" in builder
    assert "from powermem.mcp.server import main" not in builder


def test_release_binary_tarball_bin_contents_are_exact() -> None:
    smoke_script = (ROOT / "scripts" / "smoke_binary_package.py").read_text()

    assert 'f"powermem{exe_suffix}"' in smoke_script
    assert 'f"powermem-server{exe_suffix}"' in smoke_script
    assert 'f"powermem-mcp{exe_suffix}"' in smoke_script
    assert "actual != expected" in smoke_script


def test_release_binary_server_smoke_checks_health_json_and_dashboard() -> None:
    smoke_script = (ROOT / "scripts" / "smoke_binary_package.py").read_text()

    assert "/api/v1/system/health" in smoke_script
    assert "/dashboard/" in smoke_script
    assert 'ready_timeout = 180 if os.name == "nt" else 60' in smoke_script
    assert '"taskkill", "/F", "/T", "/PID"' in smoke_script
    assert 'ignore_cleanup_errors=os.name == "nt"' in smoke_script
    assert "powermem-server-smoke-output.txt" in smoke_script
    assert "stdout=server_output" in smoke_script
    assert "log_path" not in smoke_script
    assert "server_output_path.read_text" in smoke_script
    assert '"AGENT_MEMORY_MODE": "multi_user"' in smoke_script
    assert 'health.get("success") is not True' in smoke_script
    assert 'health.get("data", {}).get("status") != "healthy"' in smoke_script


def test_release_binary_smoke_checks_individual_binary_assets() -> None:
    smoke_script = (ROOT / "scripts" / "smoke_binary_package.py").read_text()

    assert "--require-individual-assets" in smoke_script
    assert "def _verify_individual_binary_assets(" in smoke_script
    assert 'asset = dist / f"{package_name}-{binary_file_name}"' in smoke_script
    assert "_verify_checksum(asset)" in smoke_script
    assert "_sha256(asset) != _sha256(packaged_binary)" in smoke_script
    assert "if args.require_individual_assets:" in smoke_script


def test_release_binary_builder_accepts_platform_and_arch_targets() -> None:
    builder = (ROOT / "scripts" / "build_binary_package.sh").read_text()

    assert "POWERMEM_BINARY_OS" in builder
    assert "POWERMEM_BINARY_ARCH" in builder
    assert (
        'PACKAGE_BASENAME="powermem-${VERSION}-${TARGET_OS}-${TARGET_ARCH}"' in builder
    )
    assert "POWERMEM_BINARY_FORMAT" in builder
    assert ".sha256" in builder


def test_release_binary_builder_exports_individual_binary_assets() -> None:
    builder = (ROOT / "scripts" / "build_binary_package.sh").read_text()

    assert (
        'local asset="${DIST}/${PACKAGE_BASENAME}-${binary_name}${EXE_SUFFIX}"'
        in builder
    )
    assert "SINGLE_BINARY_FILES" in builder
    assert "publish_binary_asset powermem\n" in builder
    assert "publish_binary_asset powermem-server" in builder
    assert "publish_binary_asset powermem-mcp" in builder
    assert 'write_sha256 "${asset}"' in builder


def test_release_binary_builder_writes_individual_assets(tmp_path: Path) -> None:
    work = tmp_path / "work"
    scripts_dir = work / "scripts"
    scripts_dir.mkdir(parents=True)
    shutil.copy2(ROOT / "scripts" / "build_binary_package.sh", scripts_dir)
    shutil.copy2(ROOT / "pyproject.toml", work / "pyproject.toml")

    fake_python = tmp_path / "fake-python"
    fake_python.write_text(
        textwrap.dedent(f"""\
            #!{sys.executable}
            import os
            import pathlib
            import sys

            if len(sys.argv) >= 3 and sys.argv[1:3] == ["-m", "PyInstaller"]:
                args = sys.argv[3:]
                name = args[args.index("--name") + 1]
                distpath = pathlib.Path(args[args.index("--distpath") + 1])
                distpath.mkdir(parents=True, exist_ok=True)
                output = distpath / name
                output.write_text(f"fake binary {{name}}\\n", encoding="utf-8")
                output.chmod(0o755)
                raise SystemExit(0)

            os.execv({sys.executable!r}, [{sys.executable!r}, *sys.argv[1:]])
            """),
        encoding="utf-8",
    )
    fake_python.chmod(0o755)

    env = os.environ.copy()
    env.update(
        {
            "PYTHON": str(fake_python),
            "POWERMEM_BINARY_OS": "macos",
            "POWERMEM_BINARY_ARCH": "aarch64",
            "POWERMEM_BINARY_FORMAT": "tar.gz",
        }
    )
    result = subprocess.run(
        ["bash", "scripts/build_binary_package.sh"],
        cwd=work,
        env=env,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    assert result.returncode == 0, result.stdout

    dist = work / "dist-binaries"
    archive = next(dist.glob("powermem-*-macos-aarch64-binaries.tar.gz"))
    package_name = _package_name(archive)
    expected_assets = [
        dist / f"{package_name}-powermem",
        dist / f"{package_name}-powermem-server",
        dist / f"{package_name}-powermem-mcp",
    ]

    assert archive.is_file()
    assert archive.with_name(f"{archive.name}.sha256").is_file()
    for asset in expected_assets:
        assert asset.is_file()
        assert asset.with_name(f"{asset.name}.sha256").is_file()


def test_release_binary_zip_packaging_keeps_uv_in_project_root() -> None:
    builder = (ROOT / "scripts" / "build_binary_package.sh").read_text()

    assert '( cd "${DIST}" && "${PYTHON_CMD[@]}" -m zipfile' not in builder
    assert (
        'zipfile.ZipFile(archive_file, "w", compression=zipfile.ZIP_DEFLATED)'
        in builder
    )
    assert "path.relative_to(package_dir).as_posix()" in builder


def test_release_binary_matrix_includes_supported_macos_and_windows_arches() -> None:
    workflow = (ROOT / ".github" / "workflows" / "build.yml").read_text()

    assert "build-native-binaries:" in workflow
    assert "macos-15-intel" in workflow
    assert "macos-15" in workflow
    assert "windows-2022" in workflow
    assert "windows-11-arm" not in workflow
    assert "binary-arch: amd64" in workflow
    assert "binary-arch: aarch64" in workflow
    assert (
        "uses: astral-sh/setup-uv@08807647e7069bb48b6ef5acd8ec9567f424441b # v8.1.0"
        in workflow
    )
    assert (
        'uv run --no-project --python "3.11" python scripts/check_package_versions.py'
        in workflow
    )
    assert "run: UV_PYTHON=3.11 bash scripts/build_binary_package.sh" in workflow
    assert (
        'uv run --no-project --python "3.11" python scripts/smoke_binary_package.py'
        in workflow
    )
    assert workflow.count("--require-individual-assets") == 2


def test_linux_binary_dockerfile_pins_pyinstaller_setuptools_runtime_api() -> None:
    dockerfile = (ROOT / "docker" / "Dockerfile.binaries-centos7").read_text()
    builder = (ROOT / "scripts" / "build_binary_package.sh").read_text()

    assert "env UV_INSTALL_DIR=/usr/local/bin sh /tmp/uv-install.sh" in dockerfile
    assert (
        "uv run --no-project --python /opt/miniconda3/envs/powermem/bin/python"
        in dockerfile
    )
    assert (
        "UV_PYTHON=/opt/miniconda3/envs/powermem/bin/python bash scripts/build_linux_binaries.sh"
        in dockerfile
    )
    assert "--with pyinstaller" in builder
    assert '--with "setuptools<81"' in builder
    assert "--with wheel" in builder
    assert '--with "pillow<12.2.1"' in builder


def test_release_uploads_arch_named_binary_assets() -> None:
    workflow = (ROOT / ".github" / "workflows" / "build.yml").read_text()

    assert "name: powermem-linux-amd64-binaries" in workflow
    assert (
        "name: powermem-${{ matrix.binary-os }}-${{ matrix.binary-arch }}-binaries"
        in workflow
    )
    assert "pattern: powermem-*-binaries" in workflow
    assert "binary-artifacts/*" in workflow
    assert "find /powermem/dist-binaries -maxdepth 1 -type f" in workflow
    assert "sha256sum -c powermem-*-linux-amd64-*.sha256" in workflow
    assert "dist-binaries/powermem-*-linux-amd64-powermem*" in workflow
    assert (
        "dist-binaries/powermem-*-${{ matrix.binary-os }}-${{ matrix.binary-arch }}-powermem*"
        in workflow
    )


def test_smoke_script_maps_archive_names_to_package_dirs() -> None:
    assert (
        _package_name(Path("powermem-1.1.4-linux-amd64-binaries.tar.gz"))
        == "powermem-1.1.4-linux-amd64"
    )
    assert (
        _package_name(Path("powermem-1.1.4-windows-aarch64-binaries.zip"))
        == "powermem-1.1.4-windows-aarch64"
    )
