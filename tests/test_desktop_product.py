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

"""Product staging with small synthetic resources; no platform qualification claims."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import prepare_desktop_product as product
import pytest
from desktop_candidate import digest, json_bytes
from prepare_managed_resources import inventory

from tests.test_ops_resources import _archive, _spec

_ROOT = Path(__file__).resolve().parents[1]


def fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, str, Path]:
    root = tmp_path / "producer"
    (root / "server").mkdir(parents=True)
    (root / "ops/runtime").mkdir(parents=True)
    (root / "ops/entry.py").write_text("# fixture")
    (root / "ops/.runtime-marker").write_bytes(b"included")
    python = root / "ops/runtime/python.exe"
    python.write_bytes(b"synthetic interpreter")
    python.chmod(0o755)
    content = inventory(root / "ops")
    manifest = {
        "format_version": 1,
        "python_version": "3.13.0",
        "requirements_sha256": "d" * 64,
        "platform": "win32",
        "architecture": "amd64",
        "python_relative": "runtime/python.exe",
        "arguments": ["-I", "-S", "-B", "entry.py"],
        "installed_bytes": sum(f["size"] for f in content),
        "files_sha256": {f["path"]: f["sha256"] for f in content},
    }
    (root / "ops/bundle.json").write_bytes(json_bytes(manifest))
    archive = _archive()
    (root / "server/server.tar.gz").write_bytes(archive)
    spec = _spec(archive, source_refs=["git+https://github.com/oceanbase/powercontext@" + "a" * 40])
    (root / "server/catalog.json").write_bytes(
        json_bytes({"format_version": 1, "resources": [spec.model_dump(mode="json")]})
    )
    candidate = {
        "format_version": 1,
        "catalog_sha256": digest(root / "server/catalog.json"),
        "binding": {
            "source_commit": "a" * 40,
            "server_version": "1.0.0",
            "server_wheel_sha256": "b" * 64,
            "server_archive_sha256": digest(root / "server/server.tar.gz"),
            "ops_bundle_manifest_sha256": digest(root / "ops/bundle.json"),
        },
    }
    (root / "candidate.json").write_bytes(json_bytes(candidate))
    repo = tmp_path / "repository"
    for folder in ("connections", "transport"):
        (repo / "desktop/src-tauri/src" / folder).mkdir(parents=True)
    (repo / "desktop/src-tauri/src/transport/operations.json").write_bytes(
        json_bytes({"contractSha256": "c" * 64, "operations": {"search_memory": {}}})
    )
    (repo / "desktop/src-tauri/src/connections/compatibility.json").write_bytes(
        json_bytes([
            {
                "id": "qualified",
                "serverCommit": "a" * 40,
                "artifactSha256": "b" * 64,
                "contractSha256": "c" * 64,
                "operations": ["search_memory"],
                "evidence": "synthetic",
            }
        ])
    )
    monkeypatch.setattr(product, "ROOT", repo)
    return root, digest(root / "candidate.json"), tmp_path / "product"


def run(inputs: tuple[Path, str, Path], **changes: str) -> None:
    root, pin, output = inputs
    product.prepare(
        root,
        pin,
        output,
        "1.2.3",
        changes.get("url", "https://downloads.powercontext.org/server.tar.gz"),
        ["https://downloads.powercontext.org"],
        changes.get("qualification", "qualified"),
    )


def large_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, str, Path]:
    root, _, output = fixture(tmp_path, monkeypatch)
    for group in range(100):
        directory = root / "ops/packages" / f"group-{group:03d}"
        directory.mkdir(parents=True)
        for entry in range(151 if group < 54 else 152):
            (directory / f"module-{entry:03d}.py").write_bytes(b"synthetic module\n")
    hidden = root / "ops/.hidden-package"
    hidden.mkdir()
    (hidden / ".nested-marker").write_bytes(b"nested hidden resource")
    files = inventory(root / "ops")
    manifest_path = root / "ops/bundle.json"
    manifest = json.loads(manifest_path.read_bytes())
    content = [f for f in files if f["path"] != "bundle.json"]
    manifest["files_sha256"] = {f["path"]: f["sha256"] for f in content}
    manifest["installed_bytes"] = sum(f["size"] for f in content)
    manifest_path.write_bytes(json_bytes(manifest))
    candidate_path = root / "candidate.json"
    candidate = json.loads(candidate_path.read_bytes())
    candidate["binding"]["ops_bundle_manifest_sha256"] = digest(manifest_path)
    candidate_path.write_bytes(json_bytes(candidate))
    return root, digest(candidate_path), output


def test_product_config_stays_bounded_for_a_complete_helper_inventory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inputs = large_fixture(tmp_path, monkeypatch)
    run(inputs)
    output = inputs[2]
    config_bytes = (output / "tauri.product.conf.json").read_bytes()
    config = json.loads(config_bytes)
    assert len(inventory(output / "ops")) >= 15_146
    assert len(config_bytes) < 4096
    assert len(config["bundle"]["resources"]) == 3
    assert (output / "ops/.runtime-marker").read_bytes() == b"included"
    assert (output / "ops/.hidden-package/.nested-marker").read_bytes() == b"nested hidden resource"


@pytest.mark.skipif(
    sys.platform != "darwin" or os.environ.get("POWERCONTEXT_TEST_TAURI_PRODUCT_BUNDLE") != "1",
    reason="requires an explicitly requested native macOS Tauri product build",
)
def test_tauri_cli_bundles_the_complete_helper_inventory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    inputs = large_fixture(tmp_path, monkeypatch)
    run(inputs)
    output = inputs[2]
    pnpm = shutil.which("pnpm")
    assert pnpm is not None, "the pinned project Tauri CLI requires pnpm"
    subprocess.run(
        [
            pnpm,
            "exec",
            "tauri",
            "build",
            "--debug",
            "--bundles",
            "app",
            "--ci",
            "--no-sign",
            "--config",
            str(output / "tauri.product.conf.json"),
            # The workflow already checks generated sources and TypeScript;
            # build the frontend here without repeating generator downloads.
            "--config",
            '{"build":{"beforeBuildCommand":"pnpm exec vite build"}}',
            "--",
            "--locked",
        ],
        cwd=_ROOT / "desktop",
        check=True,
        timeout=1200,
    )
    bundle = _ROOT / "desktop/src-tauri/target/debug/bundle/macos/PowerContext Desktop.app/Contents/Resources"
    assert inventory(bundle / "ops") == inventory(output / "ops")
    assert (bundle / "ops-provision.json").read_bytes() == (output / "ops-provision.json").read_bytes()
    assert (bundle / "server/catalog.json").read_bytes() == (output / "server/catalog.json").read_bytes()


def test_product_staging_preserves_ops_and_pins_the_download(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    inputs = fixture(tmp_path, monkeypatch)
    run(inputs)
    root, _, output = inputs
    assert inventory(output / "ops") == inventory(root / "ops")
    provision = json.loads((output / "ops-provision.json").read_bytes())
    assert provision["catalog_sha256"] == digest(output / "server/catalog.json")
    catalog = json.loads((output / "server/catalog.json").read_bytes())
    assert catalog["resources"][0]["qualification_ref"] == "qualified"
    assert catalog["resources"][0]["url"] == "https://downloads.powercontext.org/server.tar.gz"
    config = json.loads((output / "tauri.product.conf.json").read_bytes())
    assert config["version"] == "1.2.3"
    assert config["identifier"] == "com.powercontext.desktop"
    assert config["bundle"]["resources"][str(output / "ops")] == "ops/"
    with pytest.raises(ValueError, match="output already exists"):
        run(inputs)
    assert inventory(output / "ops") == inventory(root / "ops")


@pytest.mark.parametrize("damage", ["ops", "archive", "catalog", "qualification", "url", "link"])
def test_product_staging_rejects_invalid_inputs_without_publishing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, damage: str
) -> None:
    inputs = fixture(tmp_path, monkeypatch)
    root, _, output = inputs
    changes = {}
    if damage == "ops":
        (root / "ops/entry.py").write_text("changed")
    elif damage == "archive":
        (root / "server/server.tar.gz").write_bytes(b"changed")
    elif damage == "catalog":
        (root / "server/catalog.json").write_bytes(b"{}")
    elif damage == "qualification":
        changes["qualification"] = "ci-fixture"
    elif damage == "url":
        changes["url"] = "https://candidate.invalid/server.tar.gz"
    else:
        try:
            (root / "ops/alias").symlink_to("entry.py")
        except OSError:
            pytest.skip("symlink creation unavailable on this host")
    with pytest.raises(ValueError):
        run(inputs, **changes)
    assert not output.exists()


@pytest.mark.parametrize("field", ["serverCommit", "artifactSha256", "contractSha256", "operations"])
def test_product_requires_exact_bundled_qualification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
) -> None:
    inputs = fixture(tmp_path, monkeypatch)
    path = product.ROOT / "desktop/src-tauri/src/connections/compatibility.json"
    profiles = json.loads(path.read_bytes())
    profiles[0][field] = [] if field == "operations" else "mismatch"
    path.write_bytes(json_bytes(profiles))
    with pytest.raises(ValueError, match="no exact bundled product qualification"):
        run(inputs)
    assert not inputs[2].exists()


def test_product_rejects_an_unsupported_manifest_even_with_a_matching_producer_pin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _, output = fixture(tmp_path, monkeypatch)
    path = root / "ops/bundle.json"
    manifest = json.loads(path.read_bytes())
    del manifest["requirements_sha256"]
    path.write_bytes(json_bytes(manifest))
    candidate = json.loads((root / "candidate.json").read_bytes())
    candidate["binding"]["ops_bundle_manifest_sha256"] = digest(path)
    (root / "candidate.json").write_bytes(json_bytes(candidate))
    with pytest.raises(ValueError, match="unsupported Ops manifest"):
        run((root, digest(root / "candidate.json"), output))
    assert not output.exists()


@pytest.mark.parametrize("relative", ["ops/product", "ops", "."])
def test_product_output_cannot_overlap_its_resource_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    relative: str,
) -> None:
    root, pin, _ = fixture(tmp_path, monkeypatch)
    original = inventory(root)
    with pytest.raises(ValueError, match="must not contain one another"):
        run((root, pin, root / relative))
    assert inventory(root) == original
