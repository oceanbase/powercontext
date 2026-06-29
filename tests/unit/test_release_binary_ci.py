from pathlib import Path

from scripts.smoke_binary_package import _package_name


ROOT = Path(__file__).resolve().parents[2]


def test_binary_builder_verifies_miniconda_installer_checksum() -> None:
    dockerfile = (ROOT / "docker" / "Dockerfile.binaries-centos7").read_text()

    assert "ARG MINICONDA_SHA256=" in dockerfile
    assert "634d76df5e489c44ade4085552b97bebc786d49245ed1a830022b0b406de5817" in dockerfile
    assert 'echo "${MINICONDA_SHA256}  /tmp/miniconda.sh" | sha256sum -c -' in dockerfile


def test_release_binary_help_smokes_are_bounded() -> None:
    smoke_script = (ROOT / "scripts" / "smoke_binary_package.py").read_text()

    assert "[str(binary), \"--help\"]" in smoke_script
    assert "timeout=60" in smoke_script
    assert "_run_checked(" in smoke_script


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
    assert "ignore_cleanup_errors=os.name == \"nt\"" in smoke_script
    assert "powermem-server-smoke-output.txt" in smoke_script
    assert "stdout=server_output" in smoke_script
    assert "log_path" not in smoke_script
    assert "server_output_path.read_text" in smoke_script
    assert '"AGENT_MEMORY_MODE": "multi_user"' in smoke_script
    assert 'health.get("success") is not True' in smoke_script
    assert 'health.get("data", {}).get("status") != "healthy"' in smoke_script


def test_release_binary_builder_accepts_platform_and_arch_targets() -> None:
    builder = (ROOT / "scripts" / "build_binary_package.sh").read_text()

    assert "POWERMEM_BINARY_OS" in builder
    assert "POWERMEM_BINARY_ARCH" in builder
    assert "PACKAGE_BASENAME=\"powermem-${VERSION}-${TARGET_OS}-${TARGET_ARCH}\"" in builder
    assert "POWERMEM_BINARY_FORMAT" in builder
    assert ".sha256" in builder


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


def test_release_uploads_arch_named_binary_assets() -> None:
    workflow = (ROOT / ".github" / "workflows" / "build.yml").read_text()

    assert "name: powermem-linux-amd64-binaries" in workflow
    assert "name: powermem-${{ matrix.binary-os }}-${{ matrix.binary-arch }}-binaries" in workflow
    assert "pattern: powermem-*-binaries" in workflow
    assert "binary-artifacts/*" in workflow


def test_smoke_script_maps_archive_names_to_package_dirs() -> None:
    assert (
        _package_name(Path("powermem-1.1.4-linux-amd64-binaries.tar.gz"))
        == "powermem-1.1.4-linux-amd64"
    )
    assert (
        _package_name(Path("powermem-1.1.4-windows-aarch64-binaries.zip"))
        == "powermem-1.1.4-windows-aarch64"
    )
