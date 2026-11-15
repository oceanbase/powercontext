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

"""Exercise the Desktop release boundary with synthetic bytes and a local GitHub stand-in."""

from __future__ import annotations

import json
import threading
from dataclasses import asdict, replace
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit
from zipfile import ZipFile

import publish_desktop_release as publication
import pytest
from desktop_publication_manifest import (
    CANDIDATE,
    MANIFEST,
    PROVISIONING,
    REPOSITORY,
    REQUIRED,
    SIGNING,
    SUFFIX,
    Asset,
    Bundle,
    PublicationError,
    asset_url,
    extract_stage,
    validate_bundle,
)

TAG = "desktop-v1.2.3"
SOURCE = "a" * 40
SERVER_SOURCE = "c" * 40
CONTRACT_SHA = "b" * 64
WHEEL_SHA = "d" * 64
REPORT = f"https://github.com/{REPOSITORY}/issues/2112"
QUALIFICATION_ID = "sqlite-product-v1"
RUN = 123
ARTIFACT = 456


def write_json(path: Path, value: dict[str, Any]) -> Asset:
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")
    return Asset.from_file(path)


def draft_handoff(root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    candidate = {
        "schema_version": 1,
        "kind": "desktop-product-candidate",
        "status": "unqualified_candidate",
        "publishable": False,
        "release_tag": TAG,
        "version": "1.2.3",
        "source_sha": SOURCE,
        "server_source_sha": SERVER_SOURCE,
        "server_version": "1.5.0",
        "contract": {"sha256": CONTRACT_SHA},
        "targets": {},
    }
    release = {
        "format_version": 1,
        "kind": "qualified-desktop-release",
        "release_tag": TAG,
        "source_sha": SOURCE,
        "candidate_run_id": RUN,
        "candidate_artifact_id": ARTIFACT,
        "targets": {},
    }
    for target in sorted(REQUIRED):
        os_name, arch = target.split("-", 1)
        names = {
            "desktop": f"powercontext-desktop-1.2.3-{target}{SUFFIX[target]}",
            "server_archive": f"powercontext-server-1.2.3-{target}.tar.gz",
            "server_catalog": f"powercontext-server-1.2.3-{target}.json",
            "ops_archive": f"powercontext-ops-1.2.3-{target}.tar.gz",
            "ops_provision": f"powercontext-ops-provision-1.2.3-{target}.json",
            "ops_bundle_manifest": f"powercontext-ops-bundle-1.2.3-{target}.json",
        }
        for role in ("desktop", "server_archive", "ops_archive", "ops_bundle_manifest"):
            (root / names[role]).write_bytes(f"test bytes for {target} {role}".encode())
        server_archive = Asset.from_file(root / names["server_archive"])
        catalog = write_json(
            root / names["server_catalog"],
            {
                "format_version": 1,
                "resources": [
                    {
                        "platform": os_name,
                        "architecture": arch,
                        "server_version": "1.5.0",
                        "python_version": "3.12.16",
                        "archive_size": server_archive.size,
                        "archive_sha256": server_archive.sha256,
                        "url": asset_url(TAG, server_archive.name),
                        "allowed_origins": ["https://github.com", "https://release-assets.githubusercontent.com"],
                        "source_refs": [f"git+https://github.com/{REPOSITORY}@{SERVER_SOURCE}"],
                        "qualification_ref": QUALIFICATION_ID,
                    }
                ],
            },
        )
        write_json(
            root / names["ops_provision"],
            {
                "format_version": 1,
                "binding_mode": "managed_catalog",
                "catalog_sha256": catalog.sha256,
                "bundle_manifest_sha256": Asset.from_file(root / names["ops_bundle_manifest"]).sha256,
            },
        )
        records = {role: asdict(Asset.from_file(root / name)) for role, name in names.items()}
        originals = {
            role: {**asset, "name": f"{target}/{asset['name']}", "sha256": "e" * 64} for role, asset in records.items()
        }
        candidate["targets"][target] = {
            "build_kind": "product",
            "runtime": {"server_version": "1.5.0", "python_version": "3.12.16"},
            "qualification": {
                "status": "unqualified_candidate",
                "matching_product_records": [{"id": QUALIFICATION_ID}],
            },
            "assets": {
                role: originals[role] for role in ("desktop", "server_archive", "server_catalog", "ops_archive")
            },
            "provenance": {
                "server_wheel": {"sha256": WHEEL_SHA},
                **{role: originals[role] for role in ("ops_provision", "ops_bundle_manifest")},
            },
        }
        release["targets"][target] = {
            "assets": records,
            "candidate_asset_sha256": {role: asset["sha256"] for role, asset in originals.items()},
            "qualification": {
                "kind": "installed-product",
                "signing": SIGNING[target],
                "clean_machine_provisioning": "passed",
                "asset_sha256": {role: asset["sha256"] for role, asset in records.items()},
                "installed_catalog_sha256": records["server_catalog"]["sha256"],
                "installed_ops_provision_sha256": records["ops_provision"]["sha256"],
                "installed_ops_bundle_manifest_sha256": records["ops_bundle_manifest"]["sha256"],
                "os_version": "test OS",
                "webview_version": "test WebView",
                "server_version": "1.5.0",
                "runtime_version": "3.12.16",
                "limitations": "test limitation",
                "report": REPORT,
                "server": {
                    "kind": "qualified-product-server",
                    "id": QUALIFICATION_ID,
                    "report": REPORT,
                    "wheel_sha256": WHEEL_SHA,
                    "contract_sha256": CONTRACT_SHA,
                    "source_sha": SERVER_SOURCE,
                },
            },
        }
    return candidate, release


def freeze(root: Path, candidate: dict[str, Any], release: dict[str, Any]) -> str:
    release["candidate_manifest_sha256"] = write_json(root / CANDIDATE, candidate).sha256
    return write_json(root / MANIFEST, release).sha256


def validate(root: Path, digest: str, *, promote: bool = False) -> Bundle:
    return validate_bundle(
        root, TAG, SOURCE, digest, candidate_run_id=RUN, candidate_artifact_id=ARTIFACT, promote=promote
    )


@pytest.mark.parametrize(
    ("fault", "error"),
    [
        ("missing-intel", "Required Windows"),
        ("fixture", "fixture or unqualified"),
        ("empty-qualified-profile", "does not carry a matching product qualification"),
        ("wrong-wheel", "does not identify the candidate Server wheel"),
        ("desktop-source-as-server-source", "does not identify the candidate Server wheel"),
        ("missing-ops", "incomplete final asset"),
        ("mismatched-installed-ops", "must bind the published catalog and Ops"),
        ("wrong-original", "identify every original candidate asset"),
        ("unsigned-candidate", "unsigned candidate installer"),
        ("corrupt", "Staged bytes differ"),
        ("stale-catalog-url", "catalog does not identify"),
        ("candidate-origin", "catalog must permit"),
        ("duplicate-name", "Duplicate release asset"),
        ("manifest-mismatch", "differs from the approved digest"),
    ],
)
def test_bad_handoffs_fail_before_publication(tmp_path, fault, error):  # noqa: C901
    candidate, release = draft_handoff(tmp_path)
    target = "macos-x86_64"
    record = release["targets"][target]
    candidate_target = candidate["targets"][target]
    if fault == "missing-intel":
        del release["targets"][target]
    elif fault == "fixture":
        record["qualification"]["server"]["id"] = "ci-legacy-fixture"
    elif fault == "empty-qualified-profile":
        candidate_target["qualification"]["matching_product_records"] = []
    elif fault == "wrong-wheel":
        record["qualification"]["server"]["wheel_sha256"] = "0" * 64
    elif fault == "desktop-source-as-server-source":
        record["qualification"]["server"]["source_sha"] = SOURCE
    elif fault == "missing-ops":
        del record["assets"]["ops_archive"]
    elif fault == "mismatched-installed-ops":
        record["qualification"]["installed_ops_provision_sha256"] = "0" * 64
    elif fault == "wrong-original":
        record["candidate_asset_sha256"]["desktop"] = "0" * 64
    elif fault == "unsigned-candidate":
        candidate_target["assets"]["desktop"]["sha256"] = record["assets"]["desktop"]["sha256"]
        record["candidate_asset_sha256"]["desktop"] = record["assets"]["desktop"]["sha256"]
    elif fault == "corrupt":
        (tmp_path / record["assets"]["desktop"]["name"]).write_bytes(b"changed")
    elif fault in {"stale-catalog-url", "candidate-origin"}:
        path = tmp_path / record["assets"]["server_catalog"]["name"]
        catalog = json.loads(path.read_bytes())
        if fault == "stale-catalog-url":
            catalog["resources"][0]["url"] = "https://candidate.invalid/server.tar.gz"
        else:
            catalog["resources"][0]["allowed_origins"].append("https://candidate.invalid")
        asset = write_json(path, catalog)
        record["assets"]["server_catalog"] = asdict(asset)
        record["qualification"]["asset_sha256"]["server_catalog"] = asset.sha256
        record["qualification"]["installed_catalog_sha256"] = asset.sha256
    elif fault == "duplicate-name":
        other = release["targets"]["macos-arm64"]["assets"]["ops_provision"]
        candidate_target["provenance"]["ops_provision"]["name"] = f"{target}/{other['name']}"
        record["assets"]["ops_provision"] = other
    digest = freeze(tmp_path, candidate, release)
    with pytest.raises(PublicationError, match=error):
        validate(tmp_path, "0" * 64 if fault == "manifest-mismatch" else digest)


def test_staged_zip_does_not_write_outside_destination(tmp_path):
    archive = tmp_path / "unsafe.zip"
    output = tmp_path / "extracted"
    output.mkdir()
    with ZipFile(archive, "w") as bundle:
        bundle.writestr("../overwrite", "escape")
    with pytest.raises(PublicationError, match="Invalid staged filename"):
        extract_stage(archive, output)
    assert not (tmp_path / "overwrite").exists()


class FakeRelease:
    def __init__(self):
        self.release = None
        self.assets = {}
        self.payloads = {}
        self.upload_attempts = []
        self.writes = []
        self.downloads = []
        self.fail_upload = None
        self.corrupt_download = None
        self.tag_sha = SOURCE

    def row(self, name, data, *, state="uploaded"):
        return {
            "id": len(self.assets) + 1,
            "name": name,
            "size": len(data),
            "state": state,
            "digest": f"sha256:{sha256(data).hexdigest()}" if state == "uploaded" else None,
            "browser_download_url": asset_url(TAG, name),
        }


@pytest.fixture
def fake_github(monkeypatch):  # noqa: C901
    fake = FakeRelease()

    class Handler(BaseHTTPRequestHandler):
        def respond(self, status, data=None):
            self.send_response(status)
            if data is None:
                self.end_headers()
            else:
                body = data if isinstance(data, bytes) else json.dumps(data).encode()
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib parameter name
            pass

        def do_GET(self):
            url = urlsplit(self.path)
            if url.path.startswith("/api/commits/"):
                self.respond(200, {"sha": fake.tag_sha})
            elif url.path == "/api/releases":
                self.respond(200, [fake.release] if fake.release else [])
            elif url.path == "/api/releases/42/assets":
                self.respond(200, list(fake.assets.values()))
            elif url.path.startswith(f"/{REPOSITORY}/releases/download/{TAG}/"):
                assert "Authorization" not in self.headers
                assert fake.release is not None and not fake.release["draft"]
                name = url.path.rsplit("/", 1)[-1]
                fake.downloads.append(name)
                self.respond(200, b"corrupt" if name == fake.corrupt_download else fake.payloads[name])
            else:
                self.respond(404)

        def do_POST(self):
            assert self.headers["Authorization"] == "Bearer local-test-token"
            url = urlsplit(self.path)
            data = self.rfile.read(int(self.headers["Content-Length"]))
            if url.path == "/api/releases":
                fake.release = {"id": 42, **json.loads(data)}
                fake.writes.append("create")
                self.respond(201, fake.release)
                return
            assert url.path == "/upload/releases/42/assets"
            name = parse_qs(url.query)["name"][0]
            fake.upload_attempts.append(name)
            if name in fake.assets:
                self.respond(422)
            elif name == fake.fail_upload:
                fake.assets[name] = fake.row(name, b"", state="starter")
                self.respond(502)
            else:
                fake.assets[name] = fake.row(name, data)
                fake.payloads[name] = data
                self.respond(201, fake.assets[name])

        def do_DELETE(self):
            asset_id = int(self.path.rsplit("/", 1)[-1])
            [name] = [name for name, row in fake.assets.items() if row["id"] == asset_id]
            assert fake.assets[name]["state"] == "starter"
            del fake.assets[name]
            fake.writes.append("delete-empty")
            self.respond(204)

        def do_PATCH(self):
            assert self.path == "/api/releases/42"
            assert fake.release is not None
            data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            fake.release.update(data)
            fake.writes.append("update")
            self.respond(200, fake.release)

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        root = f"http://127.0.0.1:{server.server_port}"
        monkeypatch.setattr(publication, "API", f"{root}/api")
        monkeypatch.setattr(publication, "UPLOAD", f"{root}/upload")
        github = publication.GitHub("local-test-token")
        real_open = github.public.open
        monkeypatch.setattr(github.public, "open", lambda url, **kwargs: real_open(root + urlsplit(url).path, **kwargs))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield github, fake
        finally:
            server.shutdown()
            thread.join(timeout=5)


@pytest.fixture
def signed_bundle(tmp_path):
    candidate, release = draft_handoff(tmp_path)
    return validate(tmp_path, freeze(tmp_path, candidate, release))


def dispatch_for(bundle):
    return publication.Dispatch(TAG, SOURCE, "prerelease", 100, 101, RUN, ARTIFACT, bundle.manifest_sha256, "")


def test_retry_recovers_partial_draft_and_preserves_uploaded_bytes(fake_github, signed_bundle):
    github, fake = fake_github
    dispatch = dispatch_for(signed_bundle)
    first, second, *_ = sorted(signed_bundle.assets)
    fake.fail_upload = second
    with pytest.raises(PublicationError, match="HTTP 502"):
        publication.publish(github, dispatch, signed_bundle)
    assert fake.release["draft"] and not fake.downloads
    assert fake.assets[first]["state"] == "uploaded" and fake.assets[second]["state"] == "starter"

    fake.fail_upload = None
    assert publication.publish(github, dispatch, signed_bundle) == 42
    assert not fake.release["draft"] and fake.release["prerelease"]
    assert fake.upload_attempts.count(first) == 1
    assert fake.upload_attempts.count(second) == 2
    assert fake.writes == ["create", "delete-empty", "update"]
    assert set(fake.downloads) == set(signed_bundle.assets)
    assert set(signed_bundle.targets) == REQUIRED

    publication.publish(github, dispatch, signed_bundle)
    assert fake.writes == ["create", "delete-empty", "update"]


def test_draft_conflict_is_detected_before_uploading_anything(fake_github, signed_bundle):
    github, fake = fake_github
    github.create_draft(signed_bundle)
    name = next(iter(signed_bundle.assets))
    fake.assets[name] = fake.row(name, b"conflicting uploaded bytes")
    with pytest.raises(PublicationError, match="will not be replaced"):
        publication.publish(github, dispatch_for(signed_bundle), signed_bundle)
    assert fake.writes == ["create"] and not fake.upload_attempts


def test_moved_tag_is_rejected_before_any_mutation(fake_github, signed_bundle):
    github, fake = fake_github
    fake.tag_sha = SERVER_SOURCE
    with pytest.raises(PublicationError, match="does not resolve"):
        publication.publish(github, dispatch_for(signed_bundle), signed_bundle)
    assert not fake.writes and not fake.upload_attempts


def test_public_corruption_blocks_promotion_until_verified(fake_github, signed_bundle):
    github, fake = fake_github
    dispatch = dispatch_for(signed_bundle)
    fake.corrupt_download = next(iter(signed_bundle.assets))
    with pytest.raises(PublicationError, match="Byte verification failed"):
        publication.publish(github, dispatch, signed_bundle)
    assert fake.release["prerelease"] and not fake.release["draft"]

    evidence = {
        "format_version": 1,
        "kind": "downloaded-desktop-provisioning",
        "release_id": 42,
        "release_manifest_sha256": signed_bundle.manifest_sha256,
        "targets": {
            target: {
                "download_url": asset_url(TAG, assets["desktop"].name),
                "asset_sha256": {role: asset.sha256 for role, asset in assets.items()},
                "clean_machine_provisioning": "passed",
                "report": REPORT,
            }
            for target, assets in signed_bundle.targets.items()
        },
    }
    path = signed_bundle.root / PROVISIONING
    wrong = write_json(path, {**evidence, "release_id": 43})
    promote = replace(dispatch, operation="promote", provisioning_sha256=wrong.sha256)
    with pytest.raises(PublicationError, match="different prerelease"):
        publication.publish(github, promote, signed_bundle)

    evidence_sha = write_json(path, evidence).sha256
    promote = replace(promote, provisioning_sha256=evidence_sha)
    with pytest.raises(PublicationError, match="Byte verification failed"):
        publication.publish(github, promote, signed_bundle)
    assert fake.release["prerelease"]
    assert fake.writes == ["create", "update"]

    fake.corrupt_download = None
    publication.publish(github, promote, signed_bundle)
    assert not fake.release["prerelease"]
    assert evidence_sha in fake.release["body"]
    assert fake.writes == ["create", "update", "update"]
    publication.publish(github, promote, signed_bundle)
    assert fake.writes == ["create", "update", "update"]


def test_optional_candidate_does_not_block_required_publication(tmp_path):
    candidate, release = draft_handoff(tmp_path)
    candidate["targets"]["linux-x86_64"] = {"build_kind": "unqualified-optional"}
    bundle = validate(tmp_path, freeze(tmp_path, candidate, release))
    assert set(bundle.targets) == REQUIRED


def test_unprotected_dispatch_is_rejected_before_loading_inputs(monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", REPOSITORY)
    monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
    monkeypatch.setenv("GITHUB_REF", "refs/heads/master")
    monkeypatch.setenv("GITHUB_REF_PROTECTED", "false")
    monkeypatch.delenv("GITHUB_EVENT_PATH", raising=False)
    with pytest.raises(PublicationError, match="protected upstream master"):
        publication.Dispatch.current()


def test_public_redirect_cannot_use_a_non_github_origin():
    from urllib.request import Request

    with pytest.raises(PublicationError, match="Unexpected public asset redirect"):
        publication.PublicRedirect().redirect_request(
            Request(asset_url(TAG, "example.dmg")),  # noqa: S310 - fixed GitHub URL, no request sent
            None,
            302,
            "",
            {},
            "https://release-assets.githubusercontent.com.example.com/file",
        )
