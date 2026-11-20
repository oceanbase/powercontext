#!/usr/bin/env python3
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

"""Private real-runner cache content probe; no repository runtime changes."""

import json
import os
import sys
from pathlib import Path

ROOT = Path.cwd()
BASE = ROOT / ".cache-post-probe"
RUN = os.environ["GITHUB_RUN_ID"]
GEN = os.environ["PROBE_GENERATION"]
STAGE = os.environ["PROBE_STAGE"]
IDENTITY = f"cache-post-{RUN}-{os.environ['GITHUB_RUN_ATTEMPT']}-g{GEN}"


def prepare():
    BASE.mkdir(exist_ok=True)
    (BASE / "receipts").mkdir(exist_ok=True)
    (BASE / "identity.txt").write_text(IDENTITY)
    (ROOT / "pyproject.toml").write_text(
        '[project]\nname = "cache-post-probe"\nversion = "0.0.0"\nrequires-python = ">=3.12"\ndependencies = []\n[tool.uv]\npackage = false\n'
    )
    (ROOT / "uv.lock").write_text(
        'version = 1\nrevision = 3\nrequires-python = ">=3.12"\n\n[[package]]\nname = "cache-post-probe"\nversion = "0.0.0"\nsource = { virtual = "." }\n'
    )
    for label in ("a", "b"):
        project = BASE / RUN / f"pnpm-{label}"
        project.mkdir(parents=True)
        (project / "package.json").write_text('{"private": true}\n')
        (project / "pnpm-lock.yaml").write_text(f"# {IDENTITY}-{label}\nlockfileVersion: '9.0'\n")
        store = (BASE / "cache" / f"pnpm-{label}").as_posix()
        (project / "pnpm-workspace.yaml").write_text(f"storeDir: {json.dumps(store)}\n")
    (BASE / "go.mod").write_text("module example.invalid/cache-post-probe\n\ngo 1.27.0\n")
    values = {
        "UV_CACHE_DIR": BASE / "cache/uv",
        "GOMODCACHE": BASE / "cache/go-mod",
        "GOCACHE": BASE / "cache/go-build",
    }
    with open(os.environ["GITHUB_ENV"], "a", encoding="utf-8") as f:
        for key, path in values.items():
            f.write(f"{key}={path.as_posix()}\n")
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
        f.write(f"output-path={(BASE / 'cache/output').as_posix()}\n")
        f.write(f"prefix={IDENTITY}-{os.environ['RUNNER_OS']}\n")


def check():
    checks = []
    for label in ("input", "output", "pnpm_a", "pnpm_b", "uv", "go"):
        outputs = json.loads(os.environ[f"RESULT_{label.upper()}"])
        path = {"input": BASE / "cache/input", "output": BASE / "cache/output", "go": BASE / "cache/go-mod"}.get(label)
        if path is None:
            path = Path(outputs["cache-path"])
        marker = path / f"probe-{label}.json"
        expected = {"identity": IDENTITY, "label": label, "generation": GEN}
        actual = json.loads(marker.read_text()) if marker.exists() else None
        hit = outputs.get("cache-hit", "")
        ok = (hit == "true" and actual == expected) if STAGE == "verify" else hit != "true"
        checks.append({
            "label": label,
            "hit": hit,
            "key": outputs.get("cache-key", outputs.get("cache-primary-key")),
            "path": str(path),
            "sentinel_before": actual,
            "expected": expected,
            "ok": ok,
        })
        if STAGE == "seed":
            path.mkdir(parents=True, exist_ok=True)
            marker.write_text(json.dumps(expected, sort_keys=True) + "\n")
        if label in ("pnpm_a", "pnpm_b"):
            other = "pnpm_b" if label == "pnpm_a" else "pnpm_a"
            checks[-1]["other_store_marker_absent"] = not (path / f"probe-{other}.json").exists()
            checks[-1]["ok"] = bool(checks[-1]["ok"]) and bool(checks[-1]["other_store_marker_absent"])
    receipt = {
        "run_id": RUN,
        "attempt": os.environ["GITHUB_RUN_ATTEMPT"],
        "head": os.environ["GITHUB_SHA"],
        "runner_name": os.environ["RUNNER_NAME"],
        "os": os.environ["RUNNER_OS"],
        "generation": GEN,
        "stage": STAGE,
        "identity": IDENTITY,
        "checks": checks,
    }
    (BASE / "receipts/result.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt, indent=2))
    if not all(c["ok"] for c in checks):
        raise SystemExit(1)


if __name__ == "__main__":
    {"prepare": prepare, "check": check}[sys.argv[1]]()
