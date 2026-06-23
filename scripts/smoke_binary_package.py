#!/usr/bin/env python3
"""Smoke test a packaged PowerMem binary archive."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path


def _fail(message: str) -> None:
    raise SystemExit(f"error: {message}")


def _find_one(dist: Path, patterns: list[str]) -> Path:
    matches: list[Path] = []
    for pattern in patterns:
        matches.extend(dist.glob(pattern))
    if not matches:
        _fail(f"no archive found in {dist} for patterns: {', '.join(patterns)}")
    if len(matches) > 1:
        _fail(f"multiple archives found: {', '.join(str(path) for path in matches)}")
    return matches[0]


def _package_name(archive: Path) -> str:
    if archive.name.endswith(".tar.gz"):
        name = archive.name[: -len(".tar.gz")]
        return _strip_suffix(name, "-binaries")
    if archive.name.endswith(".zip"):
        name = archive.name[: -len(".zip")]
        return _strip_suffix(name, "-binaries")
    _fail(f"unsupported archive format: {archive.name}")


def _strip_suffix(value: str, suffix: str) -> str:
    if value.endswith(suffix):
        return value[: -len(suffix)]
    return value


def _verify_checksum(archive: Path) -> None:
    checksum_path = archive.with_name(f"{archive.name}.sha256")
    if not checksum_path.is_file():
        _fail(f"missing checksum file: {checksum_path.name}")

    expected = checksum_path.read_text(encoding="utf-8").split()[0]
    actual = hashlib.sha256(archive.read_bytes()).hexdigest()
    if actual != expected:
        _fail(f"checksum mismatch for {archive.name}: expected {expected}, got {actual}")


def _extract(archive: Path, target: Path) -> None:
    if archive.name.endswith(".tar.gz"):
        with tarfile.open(archive, "r:gz") as tar:
            try:
                tar.extractall(target, filter="data")
            except TypeError:
                _verify_tar_members(tar, target)
                tar.extractall(target)
        return

    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as zip_file:
            zip_file.extractall(target)
        return

    _fail(f"unsupported archive format: {archive.name}")


def _verify_tar_members(tar: tarfile.TarFile, target: Path) -> None:
    root = target.resolve()
    for member in tar.getmembers():
        member_path = (target / member.name).resolve()
        if root != member_path and root not in member_path.parents:
            _fail(f"unsafe tar member path: {member.name}")
        if member.issym() or member.islnk():
            _fail(f"unsafe tar member link: {member.name}")


def _temporary_directory(prefix: str) -> tempfile.TemporaryDirectory[str]:
    return tempfile.TemporaryDirectory(
        prefix=prefix,
        ignore_cleanup_errors=os.name == "nt",
    )


def _run_checked(
    command: list[str],
    *,
    timeout: int,
    description: str,
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            command,
            timeout=timeout,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout or ""
        if isinstance(output, bytes):
            output = output.decode(errors="replace")
        _fail(f"{description} timed out after {timeout} seconds\n{output}")

    if result.returncode != 0:
        _fail(
            f"{description} exited with status {result.returncode}\n"
            f"{result.stdout}"
        )
    return result


def _run_help(binary: Path) -> None:
    _run_checked(
        [str(binary), "--help"],
        timeout=60,
        description=f"{binary.name} --help",
    )


def _fetch(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=2) as response:
        return response.read()


def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return

    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                check=False,
                timeout=15,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
        except (OSError, subprocess.TimeoutExpired):
            process.kill()
    else:
        process.terminate()

    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=15)


def _smoke_server(server_binary: Path, port: int) -> None:
    env = os.environ.copy()
    with _temporary_directory(prefix="powermem-server-smoke-") as tmp:
        server_output_path = Path(tmp) / "powermem-server-smoke-output.txt"
        env.update(
            {
                "CI": "1",
                "DATABASE_PROVIDER": "sqlite",
                "SQLITE_PATH": str(Path(tmp) / "powermem-binary-smoke.db"),
                "LLM_PROVIDER": "noop",
                "EMBEDDING_PROVIDER": "mock",
                "RERANKER_ENABLED": "false",
                "AGENT_MEMORY_MODE": "multi_user",
                "POWERMEM_SERVER_LOG_FILE": "",
            }
        )

        with server_output_path.open("w", encoding="utf-8", errors="replace") as server_output:
            process = subprocess.Popen(
                [
                    str(server_binary),
                    "--host",
                    "localhost",
                    "--port",
                    str(port),
                    "--workers",
                    "1",
                    "--no-open-browser",
                ],
                env=env,
                stdout=server_output,
                stderr=subprocess.STDOUT,
                text=True,
            )

            failure: str | None = None
            try:
                ready_timeout = 180 if os.name == "nt" else 60
                deadline = time.monotonic() + ready_timeout
                health_body: bytes | None = None
                dashboard_body: bytes | None = None
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        failure = (
                            "powermem-server exited before becoming ready "
                            f"with code {process.returncode}"
                        )
                        break
                    try:
                        health_body = _fetch(f"http://localhost:{port}/api/v1/system/health")
                        dashboard_body = _fetch(f"http://localhost:{port}/dashboard/")
                        break
                    except urllib.error.HTTPError as exc:
                        if exc.code == 404:
                            failure = "dashboard endpoint returned 404"
                            break
                        time.sleep(1)
                    except (OSError, urllib.error.URLError):
                        time.sleep(1)

                if failure is None and (health_body is None or dashboard_body is None):
                    failure = (
                        "powermem-server did not become ready before timeout "
                        f"({ready_timeout} seconds)"
                    )

                if failure is None:
                    assert health_body is not None
                    assert dashboard_body is not None
                    health = json.loads(health_body.decode("utf-8"))
                    if health.get("success") is not True:
                        failure = f"health response did not report success=true: {health}"
                    elif health.get("data", {}).get("status") != "healthy":
                        failure = f"health response did not report status=healthy: {health}"
                    elif not dashboard_body.strip():
                        failure = "dashboard response body is empty"
            finally:
                _terminate_process_tree(process)

        if failure is not None:
            output = server_output_path.read_text(encoding="utf-8", errors="replace")
            _fail(f"{failure}\n{output}")


def _smoke_mcp(mcp_binary: Path) -> None:
    try:
        result = subprocess.run(
            [str(mcp_binary), "stdio"],
            input=b"",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
        )
    except subprocess.TimeoutExpired:
        return

    if result.returncode != 0:
        _fail(
            "powermem-mcp stdio exited with non-zero status "
            f"{result.returncode}: {result.stderr.decode(errors='replace')}"
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dist", default="dist-binaries", type=Path)
    parser.add_argument("--target-os", required=True)
    parser.add_argument("--target-arch", required=True)
    parser.add_argument("--port", default=18848, type=int)
    args = parser.parse_args()

    dist = args.dist.resolve()
    archive = _find_one(
        dist,
        [
            f"powermem-*-{args.target_os}-{args.target_arch}-binaries.tar.gz",
            f"powermem-*-{args.target_os}-{args.target_arch}-binaries.zip",
        ],
    )
    _verify_checksum(archive)

    with _temporary_directory(prefix="powermem-binary-smoke-") as tmp:
        extract_root = Path(tmp)
        _extract(archive, extract_root)
        package_root = extract_root / _package_name(archive)
        bin_dir = package_root / "bin"
        if not bin_dir.is_dir():
            _fail(f"missing bin directory: {bin_dir}")

        exe_suffix = ".exe" if args.target_os == "windows" else ""
        expected = {
            f"powermem{exe_suffix}",
            f"powermem-server{exe_suffix}",
            f"powermem-mcp{exe_suffix}",
        }
        actual = {path.name for path in bin_dir.iterdir() if path.is_file()}
        if actual != expected:
            _fail(f"unexpected bin contents: expected {sorted(expected)}, got {sorted(actual)}")

        binaries = {
            name: bin_dir / f"{name}{exe_suffix}"
            for name in ("powermem", "powermem-server", "powermem-mcp")
        }
        for binary in binaries.values():
            if args.target_os != "windows" and not os.access(binary, os.X_OK):
                _fail(f"binary is not executable: {binary}")

        _run_help(binaries["powermem"])
        _run_help(binaries["powermem-server"])
        _smoke_server(binaries["powermem-server"], args.port)
        _smoke_mcp(binaries["powermem-mcp"])

    print(f"Smoke test passed for {archive.name}")


if __name__ == "__main__":
    main()
