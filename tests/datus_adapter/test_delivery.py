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

"""Real local Server/SDK package lifecycle; these are not Agent benchmark runs."""

import asyncio
import base64
import io
import json
import os
import subprocess
import zipfile
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from powercontext_datus.delivery import deliver_skill, install_package
from powercontext_datus.freeze import IntegrityError, snapshot

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.client import PowerContextClient
from powercontext.client.errors import ServerResponseError
from powercontext.http import ArtifactReference, SkillPackageDownload, SkillPackageManifest
from powercontext.server.factory import create_server_app
from powercontext.server.settings import McpConfig, ServerSettings


def archive_bytes(*, script=False):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "SKILL.md",
            "---\nname: adapter-smoke\ndescription: Component fixture, not a learned Skill.\n---\n\nUse read-only SQL.\n",
        )
        if script:
            entry = zipfile.ZipInfo("scripts/run.sh")
            entry.external_attr = 0o100755 << 16
            archive.writestr(entry, "exit 0\n")
    return buffer.getvalue()


@pytest.fixture
def server(tmp_path):
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'server.db'}"),
            mcp=McpConfig(enabled=False),
        )
    )
    with TestClient(app) as client:
        scope = client.post(
            "/v1/scopes",
            json={"title": "Datus component test", "summary": "Offline bridge test", "idempotency_key": "datus"},
        )
        assert scope.status_code == 201, scope.text
        yield app, client, scope.json()["scope_id"]


def approved(client, scope, archive):
    proposed = client.post(
        "/v1/skill/package/propose",
        json={
            "scope_id": scope,
            "archive_base64": base64.b64encode(archive).decode(),
            "reason": "Component test",
        },
    )
    assert proposed.status_code == 201, proposed.text
    candidate = proposed.json()
    approved_response = client.post(
        "/v1/artifact-candidates/approve",
        json={
            "scope_id": scope,
            "candidate_id": candidate["candidate_id"],
            "expected_version": candidate["version"],
        },
    )
    assert approved_response.status_code == 200, approved_response.text
    ref = approved_response.json()["result_artifact"]
    manifest = client.post("/v1/skill/package/manifest", json={"scope_id": scope, "artifact": ref})
    download = client.post("/v1/skill/package/download", json={"scope_id": scope, "artifact": ref})
    assert manifest.status_code == download.status_code == 200
    return (
        ref,
        SkillPackageManifest.model_validate(manifest.json()),
        SkillPackageDownload.model_validate(download.json()),
    )


def test_approved_exact_revision_server_sdk_and_owned_destination(server, tmp_path):
    app, client, scope = server
    ref, manifest, _ = approved(client, scope, archive_bytes())
    root = tmp_path / "skills"
    root.mkdir()

    async def deliver():
        async with (
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as transport,
            PowerContextClient("http://localhost", http_client=transport) as sdk,
        ):
            return await deliver_skill(
                sdk, scope_id=scope, artifact=ArtifactReference.model_validate(ref), skill_root=root
            )

    receipt = asyncio.run(deliver())
    assert receipt["artifact"] == ref
    assert receipt["tree_digest"] == manifest.package.tree_digest
    assert receipt["files"] == snapshot(root / "adapter-smoke")
    original = snapshot(root)
    with pytest.raises(FileExistsError):
        asyncio.run(deliver())
    assert snapshot(root) == original


def test_digest_inventory_and_executable_packages_fail_closed(server, tmp_path):
    _, client, scope = server
    _, manifest, download = approved(client, scope, archive_bytes())
    root = tmp_path / "skills"
    root.mkdir()
    corrupt = download.model_copy(update={"archive_base64": base64.b64encode(b"invalid").decode()})
    with pytest.raises(IntegrityError, match="exact package"):
        install_package(manifest, corrupt, root)
    wrong_name = manifest.model_copy(update={"name": "../outside"})
    with pytest.raises(IntegrityError, match="metadata"):
        install_package(wrong_name, download, root)
    wrong_inventory = manifest.model_copy(update={"files": []})
    with pytest.raises(IntegrityError, match="inventory"):
        install_package(wrong_inventory, download, root)
    _, executable_manifest, executable_download = approved(client, scope, archive_bytes(script=True))
    with pytest.raises(IntegrityError, match="executable"):
        install_package(executable_manifest, executable_download, root)
    assert list(root.iterdir()) == []


def test_exact_revision_lookup_does_not_resolve_nonexistent_revision(server, tmp_path):
    app, client, scope = server
    ref, _, _ = approved(client, scope, archive_bytes())
    root = tmp_path / "skills"
    root.mkdir()

    async def deliver():
        async with (
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as transport,
            PowerContextClient("http://localhost", http_client=transport) as sdk,
        ):
            return await deliver_skill(
                sdk,
                scope_id=scope,
                artifact=ArtifactReference.model_validate({**ref, "revision": ref["revision"] + 1}),
                skill_root=root,
            )

    with pytest.raises(ServerResponseError):
        asyncio.run(deliver())
    assert list(root.iterdir()) == []


def test_approved_package_roundtrip_into_real_native_loader(server, tmp_path):
    configured = os.environ.get("DATUS_RUNTIME_PYTHON")
    if not configured:
        pytest.skip("requires the separately locked Datus runtime")
    _, client, scope = server
    _, manifest, download = approved(client, scope, archive_bytes())
    root = tmp_path / "skills"
    root.mkdir()
    receipt = install_package(manifest, download, root)
    source = Path(__file__).resolve().parents[2] / "integrations/datus/src"
    result = subprocess.run(
        [
            str(Path(configured).absolute()),
            "-m",
            "powercontext_datus.native",
            "--skill-root",
            str(root),
            "--expected-skill",
            "adapter-smoke",
        ],
        env={"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(source)},
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    report = json.loads(result.stdout)
    assert report["skills"]["inventory"] == ["adapter-smoke"]
    assert report["native_agent_qa_runs"] == 0
    assert receipt["files"] == snapshot(root / "adapter-smoke")
