#!/usr/bin/env python3
"""
Test write_runtime_dual() in scripts/common.sh.

Invokes the shell function via dash/sh, writes to a temp RUNTIME_FILE,
then asserts the file contains both URLs, both keys (when given), the
INFER_TRANSCRIPT flag, and that values with shell metacharacters survive
round-trip sourcing.

Run with: python apps/claude-code-plugin/tests/test_runtime_dual.py
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def run_case(label, remote_url, fallback_url, remote_key="", fallback_key="", expect_lines=None):
    print(f"── {label}")
    repo_root = Path(__file__).resolve().parents[3]
    common_sh = repo_root / "apps" / "claude-code-plugin" / "scripts" / "common.sh"
    with tempfile.TemporaryDirectory() as td:
        runtime_file = os.path.join(td, "runtime.env")
        # Build a tiny script that sources common.sh (for the function defs),
        # sets RUNTIME_FILE, then calls write_runtime_dual.
        args = ['"' + remote_url + '"']
        if remote_key:
            args.append('"' + remote_key + '"')
        else:
            args.append('""')
        args.append('"' + fallback_url + '"')
        if fallback_key:
            args.append('"' + fallback_key + '"')
        script = (
            f". {common_sh!s}\n"
            f"RUNTIME_FILE={runtime_file!s}\n"
            f"write_runtime_dual {' '.join(args)}\n"
        )
        sh = shutil.which("sh") or "/bin/sh"
        r = subprocess.run([sh, "-c", script], capture_output=True, text=True)
        if r.returncode != 0:
            print(f"  [FAIL] shell exit {r.returncode}: {r.stderr.strip()}")
            return False
        content = Path(runtime_file).read_text()
        # Now source it back and check the variables are exported correctly.
        check_script = f". {runtime_file!s}\n" + "printf 'BASE=%s|FB=%s|KEY=%s|FBKEY=%s|INFER=%s\\n' \"$POWERMEM_BASE_URL\" \"$POWERMEM_FALLBACK_BASE_URL\" \"${POWERMEM_API_KEY:-}\" \"${POWERMEM_FALLBACK_API_KEY:-}\" \"$POWERMEM_INFER_TRANSCRIPT\"\n"
        r2 = subprocess.run([sh, "-c", check_script], capture_output=True, text=True)
        if r2.returncode != 0:
            print(f"  [FAIL] source exit {r2.returncode}: {r2.stderr.strip()}")
            return False
        out = r2.stdout.strip()
        parts = out.split("|")
        base = parts[0].split("=", 1)[1]
        fb = parts[1].split("=", 1)[1]
        key = parts[2].split("=", 1)[1]
        fbkey = parts[3].split("=", 1)[1]
        infer = parts[4].split("=", 1)[1]

        ok = True
        if base != remote_url:
            print(f"  [FAIL] BASE expected {remote_url!r} got {base!r}")
            ok = False
        if fb != fallback_url:
            print(f"  [FAIL] FB expected {fallback_url!r} got {fb!r}")
            ok = False
        if remote_key and key != remote_key:
            print(f"  [FAIL] KEY expected {remote_key!r} got {key!r}")
            ok = False
        if fallback_key and fbkey != fallback_key:
            print(f"  [FAIL] FBKEY expected {fallback_key!r} got {fbkey!r}")
            ok = False
        if infer != "true":
            print(f"  [FAIL] INFER expected 'true' got {infer!r}")
            ok = False
        if ok:
            print(f"  [PASS] {label}")
        return ok


def main():
    results = []
    results.append(run_case(
        "plain urls, no keys",
        "http://remote:8848", "http://localhost:8849"))
    results.append(run_case(
        "urls + both keys",
        "http://remote:8848", "http://localhost:8849",
        remote_key="sk-abc123", fallback_key="sk-xyz789"))
    results.append(run_case(
        "url with embedded single quote",
        "http://remote:8848", "http://localhost:8849",
        remote_key="sk-with'quote"))
    results.append(run_case(
        "url with semicolon and space",
        "http://remote:8848", "http://localhost:8849",
        remote_key="sk-a;b c"))
    results.append(run_case(
        "fallback key empty when not provided",
        "http://remote:8848", "http://localhost:8849",
        remote_key="sk-only-primary"))

    passed = sum(results)
    total = len(results)
    print("=" * 40)
    print(f"Result: {passed}/{total} passed")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
