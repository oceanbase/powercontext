import os
import subprocess
import textwrap
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
COMMON_SH = ROOT / "apps" / "claude-code-plugin" / "scripts" / "common.sh"
INIT_SH = ROOT / "apps" / "claude-code-plugin" / "scripts" / "init.sh"
STOP_SH = ROOT / "apps" / "claude-code-plugin" / "scripts" / "stop.sh"
RUN_HOOK_SH = ROOT / "apps" / "claude-code-plugin" / "hooks" / "run-hook.sh"
SCRIPT_ARG0 = ROOT / "apps" / "claude-code-plugin" / "scripts" / "init.sh"


def run_common(script: str, tmp_path: Path, *, bin_dir: Path | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["HOME"] = str(tmp_path)
    env["PATH"] = f"{bin_dir or tmp_path / 'bin'}:/usr/bin:/bin"
    env["POWERMEM_DATA_DIR"] = str(tmp_path / ".powermem")
    command = textwrap.dedent(
        f"""
        set -eu
        . "{COMMON_SH}"
        {script}
        """
    )
    return subprocess.run(
        ["sh", "-c", command, str(SCRIPT_ARG0)],
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def write_executable(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content), encoding="utf-8")
    path.chmod(0o755)


def run_stop_script(tmp_path: Path, *, bin_dir: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["HOME"] = str(tmp_path)
    env["PATH"] = f"{bin_dir}:/usr/bin:/bin"
    env["POWERMEM_DATA_DIR"] = str(tmp_path / ".powermem")
    command = textwrap.dedent(
        f"""
        set -eu
        kill() {{
          printf '%s\\n' "$*" >> "$HOME/kill_calls"
          return 0
        }}
        sleep() {{ :; }}
        . "{STOP_SH}"
        """
    )
    return subprocess.run(
        ["sh", "-c", command, str(STOP_SH)],
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def fake_curl_install_script() -> str:
    return r"""
        #!/usr/bin/env sh
        printf '%s\n' "$*" > "$HOME/curl_args"
        output=
        while [ "$#" -gt 0 ]; do
          if [ "$1" = "-o" ]; then
            shift
            output=$1
          fi
          shift
        done
        cat > "$output" <<'INSTALL'
        mkdir -p "$HOME/.local/bin"
        cat > "$HOME/.local/bin/uv" <<'UV'
        #!/usr/bin/env sh
        echo fake-uv
        UV
        chmod +x "$HOME/.local/bin/uv"
        printf '%s\n' "${UV_DOWNLOAD_URL:-}" > "$HOME/uv_download_url"
        INSTALL
    """


def test_ensure_uv_reuses_existing_uv(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    write_executable(
        bin_dir / "uv",
        """
        #!/usr/bin/env sh
        echo existing-uv
        """,
    )

    result = run_common(
        """
        ensure_uv
        printf '%s\n' "$UV_BIN"
        """,
        tmp_path,
        bin_dir=bin_dir,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(bin_dir / "uv")
    assert not (tmp_path / "curl_args").exists()


def test_find_uv_bin_detects_user_local_uv_without_path(tmp_path: Path) -> None:
    uv_bin = tmp_path / ".local" / "bin" / "uv"
    write_executable(
        uv_bin,
        """
        #!/usr/bin/env sh
        echo local-uv
        """,
    )

    result = run_common(
        """
        find_uv_bin
        """,
        tmp_path,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(uv_bin)


def test_ensure_uv_installs_from_ustc_for_cn(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    write_executable(bin_dir / "curl", fake_curl_install_script())

    result = run_common(
        """
        detect_public_ip_country() { printf 'CN\n'; }
        ensure_uv
        printf 'UV_BIN=%s\n' "$UV_BIN"
        cat "$HOME/curl_args"
        printf 'DOWNLOAD=%s\n' "$(cat "$HOME/uv_download_url")"
        """,
        tmp_path,
        bin_dir=bin_dir,
    )

    assert result.returncode == 0, result.stderr
    assert f"UV_BIN={tmp_path}/.local/bin/uv" in result.stdout
    assert "mirrors.ustc.edu.cn/github-release/astral-sh/uv/LatestRelease/uv-installer.sh" in result.stdout
    assert "DOWNLOAD=https://mirrors.ustc.edu.cn/github-release/astral-sh/uv/LatestRelease/" in result.stdout


def test_ensure_uv_falls_back_to_astral_when_ustc_installer_fails(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    write_executable(
        bin_dir / "curl",
        r"""
        #!/usr/bin/env sh
        printf '%s\n' "$*" >> "$HOME/curl_calls"
        output=
        args=$*
        while [ "$#" -gt 0 ]; do
          if [ "$1" = "-o" ]; then
            shift
            output=$1
          fi
          shift
        done
        case "$args" in
          *mirrors.ustc.edu.cn*)
            cat > "$output" <<'INSTALL'
        exit 42
        INSTALL
            ;;
          *)
            cat > "$output" <<'INSTALL'
        mkdir -p "$HOME/.local/bin"
        cat > "$HOME/.local/bin/uv" <<'UV'
        #!/usr/bin/env sh
        echo fake-uv
        UV
        chmod +x "$HOME/.local/bin/uv"
        printf '%s\n' "${UV_DOWNLOAD_URL:-}" > "$HOME/uv_download_url"
        INSTALL
            ;;
        esac
        """,
    )

    result = run_common(
        """
        detect_public_ip_country() { printf 'CN\n'; }
        ensure_uv
        printf 'UV_BIN=%s\n' "$UV_BIN"
        cat "$HOME/curl_calls"
        printf 'DOWNLOAD=%s\n' "$(cat "$HOME/uv_download_url")"
        """,
        tmp_path,
        bin_dir=bin_dir,
    )

    assert result.returncode == 0, result.stderr
    assert f"UV_BIN={tmp_path}/.local/bin/uv" in result.stdout
    assert "mirrors.ustc.edu.cn/github-release/astral-sh/uv/LatestRelease/uv-installer.sh" in result.stdout
    assert "https://astral.sh/uv/install.sh" in result.stdout
    assert "DOWNLOAD=" in result.stdout
    assert "USTC uv mirror install failed" in result.stderr


def test_detect_public_ip_country_uses_curl_without_python(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    write_executable(
        bin_dir / "curl",
        """
        #!/usr/bin/env sh
        printf 'cn\n'
        """,
    )

    result = run_common(
        """
        detect_public_ip_country
        """,
        tmp_path,
        bin_dir=bin_dir,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "CN"


def test_ensure_uv_installs_from_astral_for_non_cn(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    write_executable(bin_dir / "curl", fake_curl_install_script())

    result = run_common(
        """
        detect_public_ip_country() { printf 'US\n'; }
        ensure_uv
        printf 'UV_BIN=%s\n' "$UV_BIN"
        cat "$HOME/curl_args"
        printf 'DOWNLOAD=%s\n' "$(cat "$HOME/uv_download_url")"
        """,
        tmp_path,
        bin_dir=bin_dir,
    )

    assert result.returncode == 0, result.stderr
    assert f"UV_BIN={tmp_path}/.local/bin/uv" in result.stdout
    assert "https://astral.sh/uv/install.sh" in result.stdout
    assert "DOWNLOAD=" in result.stdout
    assert "mirrors.ustc.edu.cn" not in result.stdout


def test_uvx_run_uses_tuna_index_for_cn(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    write_executable(
        bin_dir / "uv",
        """
        #!/usr/bin/env sh
        printf '%s\n' "$*" > "$HOME/uv_args"
        """,
    )

    result = run_common(
        """
        detect_public_ip_country() { printf 'CN\n'; }
        uvx_run --python /tmp/python3.11 --from powermem[server,seekdb] powermem-server --host 127.0.0.1 --port 8848
        cat "$HOME/uv_args"
        """,
        tmp_path,
        bin_dir=bin_dir,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().endswith(
        "tool run --default-index https://pypi.tuna.tsinghua.edu.cn/simple "
        "--python /tmp/python3.11 --from powermem[server,seekdb] "
        "powermem-server --host 127.0.0.1 --port 8848"
    )


def test_export_env_file_vars_exports_generated_server_config(tmp_path: Path) -> None:
    env_file = tmp_path / ".powermem" / ".env"
    env_file.parent.mkdir(parents=True, exist_ok=True)
    env_file.write_text(
        "\n".join(
            [
                "# comment",
                "POWERMEM_SERVER_AUTH_ENABLED=false",
                "POWERMEM_SERVER_API_KEYS=",
                "INVALID-NAME=ignored",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = run_common(
        f"""
        export_env_file_vars "{env_file}"
        printf 'AUTH=%s\\n' "$POWERMEM_SERVER_AUTH_ENABLED"
        printf 'KEYS=%s\\n' "${{POWERMEM_SERVER_API_KEYS-set}}"
        printf 'INVALID=%s\\n' "${{INVALID-NAME-unset}}"
        """,
        tmp_path,
    )

    assert result.returncode == 0, result.stderr
    assert "AUTH=false" in result.stdout
    assert "KEYS=" in result.stdout
    assert "INVALID=NAME-unset" in result.stdout


def test_hook_launcher_exports_runtime_env_to_native_binary(tmp_path: Path) -> None:
    plugin_root = tmp_path / "plugin"
    hooks_dir = plugin_root / "hooks"
    bin_dir = hooks_dir / "bin"
    hooks_dir.mkdir(parents=True)
    run_hook = hooks_dir / "run-hook.sh"
    run_hook.write_text(RUN_HOOK_SH.read_text(encoding="utf-8"), encoding="utf-8")
    run_hook.chmod(0o755)
    write_executable(
        bin_dir / "powermem-hook-linux-amd64",
        """
        #!/usr/bin/env sh
        printf 'BASE=%s\n' "${POWERMEM_BASE_URL:-missing}"
        """,
    )
    data_dir = tmp_path / ".powermem"
    data_dir.mkdir()
    (data_dir / "runtime.env").write_text("POWERMEM_BASE_URL=http://localhost:18848\n", encoding="utf-8")

    result = subprocess.run(
        ["sh", str(run_hook)],
        cwd=tmp_path,
        env={**os.environ, "HOME": str(tmp_path), "POWERMEM_DATA_DIR": str(data_dir)},
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "BASE=http://localhost:18848"


def test_bootstrap_python_uses_uv_managed_python_by_default(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    uv_python = tmp_path / "uv-python-3.11"
    write_executable(
        bin_dir / "uv",
        f"""
        #!/usr/bin/env sh
        printf '%s\\n' "$*" >> "$HOME/uv_calls"
        if [ "$1" = "python" ] && [ "$2" = "install" ]; then
          exit 0
        fi
        if [ "$1" = "python" ] && [ "$2" = "find" ]; then
          printf '%s\\n' "{uv_python}"
          exit 0
        fi
        exit 1
        """,
    )

    result = run_common(
        """
        detect_public_ip_country() { printf 'US\n'; }
        ensure_bootstrap_python
        printf 'BOOTSTRAP_PYTHON=%s\n' "$BOOTSTRAP_PYTHON"
        printf 'POWERMEM_BOOTSTRAP_PYTHON=%s\n' "$POWERMEM_BOOTSTRAP_PYTHON"
        cat "$HOME/uv_calls"
        """,
        tmp_path,
        bin_dir=bin_dir,
    )

    assert result.returncode == 0, result.stderr
    assert f"BOOTSTRAP_PYTHON={uv_python}" in result.stdout
    assert f"POWERMEM_BOOTSTRAP_PYTHON={uv_python}" in result.stdout
    assert "python install 3.11" in result.stdout
    assert "python find 3.11" in result.stdout


def test_bootstrap_python_uses_ustc_python_install_mirror_for_cn(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    uv_python = tmp_path / "uv-python-3.11"
    write_executable(
        bin_dir / "uv",
        f"""
        #!/usr/bin/env sh
        printf '%s MIRROR=%s\\n' "$*" "${{UV_PYTHON_INSTALL_MIRROR:-}}" >> "$HOME/uv_calls"
        if [ "$1" = "python" ] && [ "$2" = "install" ]; then
          exit 0
        fi
        if [ "$1" = "python" ] && [ "$2" = "find" ]; then
          printf '%s\\n' "{uv_python}"
          exit 0
        fi
        exit 1
        """,
    )

    result = run_common(
        """
        detect_public_ip_country() { printf 'CN\n'; }
        ensure_bootstrap_python
        cat "$HOME/uv_calls"
        """,
        tmp_path,
        bin_dir=bin_dir,
    )

    assert result.returncode == 0, result.stderr
    assert (
        "python install 3.11 MIRROR="
        "https://mirrors.ustc.edu.cn/github-release/astral-sh/python-build-standalone/"
    ) in result.stdout
    assert "python find 3.11 MIRROR=" in result.stdout


def test_bootstrap_python_allows_explicit_python_install_mirror(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    uv_python = tmp_path / "uv-python-3.11"
    write_executable(
        bin_dir / "uv",
        f"""
        #!/usr/bin/env sh
        printf '%s MIRROR=%s\\n' "$*" "${{UV_PYTHON_INSTALL_MIRROR:-}}" >> "$HOME/uv_calls"
        if [ "$1" = "python" ] && [ "$2" = "install" ]; then
          exit 0
        fi
        if [ "$1" = "python" ] && [ "$2" = "find" ]; then
          printf '%s\\n' "{uv_python}"
          exit 0
        fi
        exit 1
        """,
    )

    result = run_common(
        """
        POWERMEM_UV_PYTHON_INSTALL_MIRROR=https://example.invalid/python-build-standalone/
        export POWERMEM_UV_PYTHON_INSTALL_MIRROR
        detect_public_ip_country() { printf 'CN\n'; }
        ensure_bootstrap_python
        cat "$HOME/uv_calls"
        """,
        tmp_path,
        bin_dir=bin_dir,
    )

    assert result.returncode == 0, result.stderr
    assert (
        "python install 3.11 MIRROR="
        "https://example.invalid/python-build-standalone/"
    ) in result.stdout
    assert "mirrors.ustc.edu.cn/github-release/astral-sh/python-build-standalone/" not in result.stdout


def test_stop_script_stops_orphaned_powermem_server_on_runtime_port(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    data_dir = tmp_path / ".powermem"
    data_dir.mkdir()
    (data_dir / "runtime.env").write_text("POWERMEM_BASE_URL=http://localhost:18848\n", encoding="utf-8")
    write_executable(
        bin_dir / "lsof",
        """
        #!/usr/bin/env sh
        printf '%s\\n' "$*" > "$HOME/lsof_args"
        printf '4242\\n'
        """,
    )
    write_executable(
        bin_dir / "ps",
        """
        #!/usr/bin/env sh
        if [ "$1" = "-p" ] && [ "$2" = "4242" ]; then
          printf '/usr/bin/powermem-server --host 127.0.0.1 --port 18848\\n'
          exit 0
        fi
        exit 1
        """,
    )
    write_executable(bin_dir / "ss", "#!/usr/bin/env sh\nexit 1\n")
    write_executable(bin_dir / "fuser", "#!/usr/bin/env sh\nexit 1\n")

    result = run_stop_script(tmp_path, bin_dir=bin_dir)

    assert result.returncode == 0, result.stderr
    assert "Stopping orphaned PowerMem server PID 4242 on port 18848" in result.stdout
    assert "No managed PowerMem server is running." not in result.stdout
    assert (tmp_path / "kill_calls").read_text(encoding="utf-8").splitlines() == [
        "4242",
        "-0 4242",
        "-9 4242",
    ]
    assert "-tiTCP:18848" in (tmp_path / "lsof_args").read_text(encoding="utf-8")


def test_stop_script_ignores_non_powermem_listener_on_runtime_port(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    data_dir = tmp_path / ".powermem"
    data_dir.mkdir()
    (data_dir / "runtime.env").write_text("POWERMEM_BASE_URL=http://localhost:18849\n", encoding="utf-8")
    write_executable(
        bin_dir / "lsof",
        """
        #!/usr/bin/env sh
        printf '4242\\n'
        """,
    )
    write_executable(
        bin_dir / "ps",
        """
        #!/usr/bin/env sh
        if [ "$1" = "-p" ] && [ "$2" = "4242" ]; then
          printf 'python3 -m http.server 18849\\n'
          exit 0
        fi
        exit 1
        """,
    )
    write_executable(bin_dir / "ss", "#!/usr/bin/env sh\nexit 1\n")
    write_executable(bin_dir / "fuser", "#!/usr/bin/env sh\nexit 1\n")

    result = run_stop_script(tmp_path, bin_dir=bin_dir)

    assert result.returncode == 0, result.stderr
    assert "No managed PowerMem server is running." in result.stdout
    assert not (tmp_path / "kill_calls").exists()


def test_init_uses_uvx_launcher_instead_of_plugin_venv_install() -> None:
    script = INIT_SH.read_text(encoding="utf-8")

    assert "ensure_bootstrap_python || exit 1" in script
    assert "BOOTSTRAP_PYTHON=$(choose_python)" not in script
    assert 'export_env_file_vars "$ENV_FILE"' in script
    assert 'tool run \\' in script
    assert "--from \"$PACKAGE\"" in script
    assert "powermem-server --host \"${POWERMEM_SERVER_HOST:-127.0.0.1}\" --port \"$port\"" in script
    assert "uv_pip_install" not in script
    assert "venv_powermem_server" not in script


def test_init_writes_absolute_sdk_log_paths_to_generated_env() -> None:
    script = INIT_SH.read_text(encoding="utf-8")

    assert 'f"LOGGING_FILE={path_value(\'powermem.log\')}"' in script
    assert 'f"AUDIT_LOG_FILE={path_value(\'audit.log\')}"' in script
    assert "LOGGING_FILE=./logs" not in script
    assert "AUDIT_LOG_FILE=./logs" not in script


def test_init_does_not_write_hf_hub_offline_to_env() -> None:
    """PowerMem's HuggingFaceEmbedding manages offline mode internally via
    SentenceTransformer(local_files_only=True) and runs an internal
    ModelScope/HF download when the cache is empty.  Forcing HF_HUB_OFFLINE=1
    globally would block that download for non-CN users.  init.sh must not
    write HF_HUB_OFFLINE to the generated .env (regression guard for
    wayyoungboy's review comment #3-1 on PR #1031).
    """
    script = INIT_SH.read_text(encoding="utf-8")

    assert 'lines.append("HF_HUB_OFFLINE=1")' not in script
    assert 'lines.append("HF_HUB_OFFLINE=' not in script
    # Any remaining HF_HUB_OFFLINE=1 must be inside comment lines only.
    for line in script.splitlines():
        stripped = line.lstrip()
        if "HF_HUB_OFFLINE=1" in stripped:
            assert stripped.startswith("#"), (
                f"HF_HUB_OFFLINE=1 outside a comment: {line!r}"
            )


def test_init_package_spec_matches_db_provider() -> None:
    """SQLite path must install powermem[server,extras] (sentence-transformers
    for the default huggingface embedder); OceanBase path must install
    powermem[server,seekdb] (pyseekdb).  Regression guard for
    wayyoungboy's review comment #1 on PR #1031.
    """
    script = INIT_SH.read_text(encoding="utf-8")

    assert 'oceanbase) PACKAGE="${POWERMEM_INIT_PACKAGE:-powermem[server,seekdb]}"' in script
    assert '*)         PACKAGE="${POWERMEM_INIT_PACKAGE:-powermem[server,extras]}"' in script
    # default (non-oceanbase) must not accidentally pull seekdb-only deps
    assert 'PACKAGE="${POWERMEM_INIT_PACKAGE:-powermem[server]}"' not in script


def test_init_preload_model_is_deprecated_no_op() -> None:
    """POWERMEM_INIT_PRELOAD_MODEL must not call preload-model.sh or otherwise
    attempt to download the embedding model — that now lives inside PowerMem's
    HuggingFaceEmbedding (upstream #1057, CN-aware: ModelScope for CN, HF for
    non-CN, local_files_only=True on cache hit).  init.sh may only print a
    deprecation message when the variable is set.
    """
    script = INIT_SH.read_text(encoding="utf-8")

    assert 'preload-model.sh' not in script.replace(
        '# preload-model.sh', ''
    ).replace(
        'preload-model.sh is deprecated', ''
    )
    assert 'sh "$SCRIPT_DIR/preload-model.sh"' not in script
    assert 'POWERMEM_INIT_PRELOAD_MODEL is deprecated' in script


def test_write_runtime_remote_quotes_metacharacters(tmp_path: Path) -> None:
    """runtime.env is sourced by run-hook.sh and status.sh. URLs / API keys
    may contain shell metacharacters ($, ;, spaces, backticks, single quotes).
    Values must be single-quoted with embedded ' escaped via the standard
    '\'' trick so sourcing round-trips the exact bytes. Regression guard for
    PR #1101 review: previously values were written bare, so a URL like
    'http://host:8001/path?x=1;y=2' was split on ';' and 'y=2' was lost.
    """
    tricky_url = "http://host:8001/path?a=1;b=2 c=3$VAR`echo hi`"
    tricky_key = "sk-abc'\"def $XYZ"

    env = os.environ.copy()
    env["TRICKY_URL"] = tricky_url
    env["TRICKY_KEY"] = tricky_key
    env["HOME"] = str(tmp_path)
    env["PATH"] = f"{tmp_path / 'bin'}:/usr/bin:/bin"
    env["POWERMEM_DATA_DIR"] = str(tmp_path / ".powermem")
    command = textwrap.dedent(
        f"""
        set -eu
        . "{COMMON_SH}"
        write_runtime_remote "$TRICKY_URL" "$TRICKY_KEY"
        cat "$RUNTIME_FILE"
        echo "---"
        . "$RUNTIME_FILE"
        printf 'URL=%s\\n' "$POWERMEM_BASE_URL"
        printf 'KEY=%s\\n' "$POWERMEM_API_KEY"
        """
    )
    result = subprocess.run(
        ["sh", "-c", command, str(SCRIPT_ARG0)],
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "POWERMEM_BASE_URL=" in result.stdout
    assert f"URL={tricky_url}" in result.stdout
    assert f"KEY={tricky_key}" in result.stdout


def test_write_runtime_hook_disabled_writes_marker(tmp_path: Path) -> None:
    """MCP-only mode must write POWERMEM_HOOK_DISABLED=1 to runtime.env so
    run-hook.sh exits early instead of falling back to a stale base URL.
    Regression guard for PR #1101 review issue: mcp mode previously wrote
    nothing, leaving stale runtime.env from a prior hook/both init.
    """
    result = run_common(
        """
        write_runtime_hook_disabled
        cat "$RUNTIME_FILE"
        """,
        tmp_path,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "POWERMEM_HOOK_DISABLED=1"


def test_run_hook_exits_early_when_hook_disabled(tmp_path: Path) -> None:
    """When runtime.env sets POWERMEM_HOOK_DISABLED=1, run-hook.sh must exit 0
    without exec'ing the native binary — so a stale POWERMEM_BASE_URL never
    gets hit. Regression guard for PR #1101 review.
    """
    plugin_root = tmp_path / "plugin"
    hooks_dir = plugin_root / "hooks"
    bin_dir = hooks_dir / "bin"
    hooks_dir.mkdir(parents=True)
    run_hook = hooks_dir / "run-hook.sh"
    run_hook.write_text(RUN_HOOK_SH.read_text(encoding="utf-8"), encoding="utf-8")
    run_hook.chmod(0o755)
    # Plant a binary that would fail the test if exec'd.
    write_executable(
        bin_dir / "powermem-hook-linux-amd64",
        """
        #!/usr/bin/env sh
        echo "BINARY-RAN"
        exit 99
        """,
    )
    data_dir = tmp_path / ".powermem"
    data_dir.mkdir()
    (data_dir / "runtime.env").write_text(
        "POWERMEM_HOOK_DISABLED=1\nPOWERMEM_BASE_URL=http://stale.example:8848\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        ["sh", str(run_hook)],
        cwd=tmp_path,
        env={**os.environ, "HOME": str(tmp_path), "POWERMEM_DATA_DIR": str(data_dir)},
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "BINARY-RAN" not in result.stdout


def test_init_mcp_branch_writes_hook_disabled_marker() -> None:
    """init.sh's mcp connection-mode branch must call write_runtime_hook_disabled
    so stale runtime.env state doesn't linger. Regression guard for PR #1101
    review.
    """
    script = INIT_SH.read_text(encoding="utf-8")

    assert "write_runtime_hook_disabled" in script
    # The mcp case must be inside the connection-mode case block, not the
    # hook|both block.
    assert "mcp)\n      write_runtime_hook_disabled" in script or \
           "mcp)\n      # Write a marker" in script
