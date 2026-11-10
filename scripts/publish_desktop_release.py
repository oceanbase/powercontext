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

"""Publish an approved Desktop signing handoff without rebuilding or replacing assets."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
from zipfile import BadZipFile

from desktop_publication_manifest import (
    CANDIDATE,
    COMMIT,
    MAX_STAGED,
    REPOSITORY,
    SHA,
    TAG,
    TAG_PREFIX,
    Asset,
    Bundle,
    PublicationError,
    asset_url,
    extract_stage,
    matching,
    require,
    validate_bundle,
    validate_provisioning,
    verify_bytes,
)

ENVIRONMENT = "desktop-release"
POSITIVE_ID = r"[1-9][0-9]{0,19}"
API = f"https://api.github.com/repos/{REPOSITORY}"
UPLOAD = f"https://uploads.github.com/repos/{REPOSITORY}"
DOWNLOAD_HOSTS = {"release-assets.githubusercontent.com", "objects.githubusercontent.com"}


class NoRedirect(HTTPRedirectHandler):
    """Never forward the write token or request body through a redirect."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # ty: ignore[missing-override-decorator]
        return None


class PublicRedirect(HTTPRedirectHandler):
    """Follow only GitHub's public asset CDN redirects, without credentials."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # ty: ignore[missing-override-decorator]
        parts = urlsplit(newurl)
        require(parts.scheme == "https" and parts.netloc in DOWNLOAD_HOSTS, "Unexpected public asset redirect")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


@dataclass(frozen=True)
class Dispatch:
    tag: str
    source_sha: str
    operation: str
    run_id: int
    artifact_id: int
    candidate_run_id: int
    candidate_artifact_id: int
    manifest_sha256: str
    provisioning_sha256: str

    @property
    def promote(self) -> bool:
        return self.operation == "promote"

    @classmethod
    def current(cls) -> Dispatch:
        require(
            os.environ.get("GITHUB_REPOSITORY") == REPOSITORY
            and os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
            and os.environ.get("GITHUB_REF") == "refs/heads/master"
            and os.environ.get("GITHUB_REF_PROTECTED") == "true",
            "Desktop release requires explicit dispatch on protected upstream master",
        )
        event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
        require(event["repository"]["default_branch"] == "master", "Unexpected upstream default branch")
        inputs = event["inputs"]
        dispatch = cls(
            matching(inputs["release_tag"], TAG, "Desktop release tag"),
            matching(inputs["source_sha"], COMMIT, "source commit"),
            matching(inputs["operation"], r"prerelease|promote", "release operation"),
            int(matching(inputs["staging_run_id"], POSITIVE_ID, "staging run ID")),
            int(matching(inputs["staging_artifact_id"], POSITIVE_ID, "staging artifact ID")),
            int(matching(inputs["candidate_run_id"], POSITIVE_ID, "candidate run ID")),
            int(matching(inputs["candidate_artifact_id"], POSITIVE_ID, "candidate manifest artifact ID")),
            matching(inputs["release_manifest_sha256"], SHA, "release manifest SHA-256"),
            inputs.get("provisioning_evidence_sha256") or "",
        )
        if dispatch.promote:
            matching(dispatch.provisioning_sha256, SHA, "downloaded provisioning evidence SHA-256")
        else:
            require(not dispatch.provisioning_sha256, "Provisioning evidence is for promote only")
        return dispatch


class GitHub:
    def __init__(self, token: str) -> None:
        self.token = token
        self.api = build_opener(ProxyHandler({}), NoRedirect())
        self.public = build_opener(ProxyHandler({}), PublicRedirect())

    def _request(
        self, url: str, method: str = "GET", *, payload: dict[str, Any] | None = None, file: Path | None = None
    ) -> Any:
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        try:
            if file is not None:
                headers.update({"Content-Type": "application/octet-stream", "Content-Length": str(file.stat().st_size)})
                with (
                    file.open("rb") as stream,
                    self.api.open(
                        Request(url, data=stream, headers=headers, method=method),  # noqa: S310
                        timeout=120,
                    ) as response,
                ):
                    return json.load(response)
            if payload is not None:
                headers["Content-Type"] = "application/json"
            data = json.dumps(payload).encode("utf-8") if payload is not None else None
            with self.api.open(Request(url, data=data, headers=headers, method=method), timeout=30) as response:  # noqa: S310
                return None if response.status == 204 else json.load(response)
        except HTTPError as error:
            raise PublicationError(f"GitHub {method} {urlsplit(url).path} returned HTTP {error.code}") from error  # noqa: TRY003

    def request(self, path: str, method: str = "GET", payload: dict[str, Any] | None = None) -> Any:
        return self._request(f"{API}/{path}", method, payload=payload)

    def tag_commit(self, tag: str) -> str:
        ref = quote("refs/tags/" + tag, safe="")
        return self.request(f"commits/{ref}")["sha"]

    def release(self, tag: str) -> dict[str, Any] | None:
        # Listing with the write token includes drafts, including partially staged drafts.
        page = 1
        matches = []
        while True:
            releases = self.request(f"releases?per_page=100&page={page}")
            matches.extend(item for item in releases if item["tag_name"] == tag)
            if len(releases) < 100:
                break
            page += 1
        require(len(matches) <= 1, "Multiple GitHub Releases use this Desktop tag")
        return matches[0] if matches else None

    def create_draft(self, bundle: Bundle) -> dict[str, Any]:
        return self.request(
            "releases",
            "POST",
            {
                "tag_name": bundle.tag,
                "target_commitish": bundle.source_sha,
                "name": f"PowerContext Desktop {bundle.tag.removeprefix(TAG_PREFIX)}",
                "body": bundle.notes(),
                "draft": True,
                "prerelease": True,
                "make_latest": "false",
            },
        )

    def assets(self, release_id: int) -> list[dict[str, Any]]:
        rows = self.request(f"releases/{release_id}/assets?per_page=100")
        require(len(rows) < 100, "GitHub Release has more assets than a Desktop release can contain")
        return rows

    def upload(self, release_id: int, file: Path) -> dict[str, Any]:
        return self._request(
            f"{UPLOAD}/releases/{release_id}/assets?name={quote(file.name, safe='')}", "POST", file=file
        )

    def remove_empty_upload(self, asset_id: int) -> None:
        self.request(f"releases/assets/{asset_id}", "DELETE")

    def update(self, release_id: int, **fields: Any) -> dict[str, Any]:
        return self.request(f"releases/{release_id}", "PATCH", {**fields, "make_latest": "false"})

    def download(self, tag: str, asset: Asset, destination: Path) -> None:
        with (
            self.public.open(asset_url(tag, asset.name), timeout=120) as response,
            destination.open("xb") as output,
        ):
            verify_bytes(response, asset, output)


def verify_tag(github: GitHub, tag: str, source_sha: str) -> None:
    require(github.tag_commit(tag) == source_sha, "Desktop tag does not resolve to the approved source commit")


def is_ancestor(source: str, head: str) -> bool:
    return (
        subprocess.run(  # noqa: S603
            ["git", "merge-base", "--is-ancestor", source, head],  # noqa: S607
            check=False,
        ).returncode
        == 0
    )


def verify_artifact(github: GitHub, run_id: int, artifact_id: int, expected_name: str) -> dict[str, Any]:
    run = github.request(f"actions/runs/{run_id}")
    require(
        run["id"] == run_id
        and run["conclusion"] == "success"
        and run["event"] == "workflow_dispatch"
        and run["head_branch"] == "master"
        and run["head_repository"]["full_name"] == REPOSITORY,
        "Handoffs must come from successful explicit runs on upstream master",
    )
    artifact = github.request(f"actions/artifacts/{artifact_id}")
    require(
        artifact["id"] == artifact_id
        and artifact["name"] == expected_name
        and 0 < artifact["size_in_bytes"] <= MAX_STAGED
        and not artifact["expired"]
        and artifact["workflow_run"]["id"] == run_id,
        "Selected artifact does not belong to the approved handoff run",
    )
    matching(artifact.get("digest"), "sha256:" + SHA, "Actions artifact digest")
    return run


def gate(github: GitHub, dispatch: Dispatch) -> None:
    head = matching(os.environ.get("GITHUB_SHA"), COMMIT, "workflow commit")
    require(is_ancestor(dispatch.source_sha, head), "Tagged source is not in the protected master history")
    verify_tag(github, dispatch.tag, dispatch.source_sha)
    environment = github.request(f"environments/{ENVIRONMENT}")
    policy = environment.get("deployment_branch_policy") or {}
    require(policy.get("protected_branches") is True, "desktop-release environment must allow protected branches only")
    require(
        any(
            rule.get("type") == "required_reviewers"
            and rule.get("prevent_self_review") is True
            and rule.get("reviewers")
            for rule in environment["protection_rules"]
        ),
        "desktop-release environment requires independent human reviewers",
    )

    version = dispatch.tag.removeprefix(TAG_PREFIX)
    candidate = verify_artifact(
        github,
        dispatch.candidate_run_id,
        dispatch.candidate_artifact_id,
        f"desktop-candidate-manifest-{version}-{dispatch.source_sha}",
    )
    require(
        candidate["path"] == ".github/workflows/desktop-candidate.yml" and candidate["head_sha"] == dispatch.source_sha,
        "Candidate manifest must originate from the 09-A workflow at the approved source commit",
    )
    stage = verify_artifact(github, dispatch.run_id, dispatch.artifact_id, "desktop-signed-release-handoff")
    require(
        is_ancestor(matching(stage["head_sha"], COMMIT, "staging workflow commit"), head),
        "Signing handoff workflow commit is not in the protected master history",
    )


def release_id_for(release: dict[str, Any], bundle: Bundle) -> int:
    require(
        release["tag_name"] == bundle.tag and (release.get("body") or "").splitlines()[:1] == [bundle.release_marker],
        "Existing GitHub Release belongs to another manifest; refusing to modify it",
    )
    require(type(release["id"]) is int and release["id"] > 0, "Invalid GitHub Release ID")
    return release["id"]


def check_remote_asset(row: dict[str, Any], asset: Asset, bundle: Bundle, *, draft: bool) -> None:
    require(
        row["name"] == asset.name
        and row["state"] == "uploaded"
        and row["size"] == asset.size
        and row.get("digest") == f"sha256:{asset.sha256}"
        and (draft or row["browser_download_url"] == asset_url(bundle.tag, asset.name)),
        f"Conflicting GitHub asset: {asset.name}. Existing uploaded bytes will not be replaced.",
    )


def remote_inventory(
    github: GitHub, release_id: int, bundle: Bundle, *, draft: bool
) -> tuple[set[str], dict[str, int]]:
    uploaded: set[str] = set()
    empty_uploads: dict[str, int] = {}
    for row in github.assets(release_id):
        name = row["name"]
        require(
            name in bundle.assets and name not in uploaded and name not in empty_uploads,
            "Unexpected or duplicate GitHub Release asset",
        )
        if draft and row["state"] == "starter" and row["size"] == 0 and not row.get("digest"):
            empty_uploads[name] = int(matching(str(row["id"]), POSITIVE_ID, "empty asset ID"))
        else:
            check_remote_asset(row, bundle.assets[name], bundle, draft=draft)
            uploaded.add(name)
    return uploaded, empty_uploads


def stage_draft(github: GitHub, release_id: int, bundle: Bundle) -> None:
    uploaded, empty_uploads = remote_inventory(github, release_id, bundle, draft=True)
    for name in sorted(bundle.assets.keys() - uploaded):
        verify_tag(github, bundle.tag, bundle.source_sha)
        if name in empty_uploads:
            # A failed GitHub upload can leave a zero-byte "starter". This is
            # not an uploaded asset; never delete a successfully uploaded row.
            github.remove_empty_upload(empty_uploads[name])
        row = github.upload(release_id, bundle.root / name)
        check_remote_asset(row, bundle.assets[name], bundle, draft=True)
    uploaded, empty_uploads = remote_inventory(github, release_id, bundle, draft=True)
    require(
        uploaded == bundle.assets.keys() and not empty_uploads, "Draft does not contain the complete approved inventory"
    )


def verify_public(github: GitHub, release_id: int, bundle: Bundle) -> None:
    verify_tag(github, bundle.tag, bundle.source_sha)
    uploaded, _ = remote_inventory(github, release_id, bundle, draft=False)
    require(uploaded == bundle.assets.keys(), "Published release is missing approved assets")
    with tempfile.TemporaryDirectory(prefix="desktop-release-download-") as directory:
        for asset in bundle.assets.values():
            path = Path(directory) / asset.name
            github.download(bundle.tag, asset, path)
            path.unlink()
    verify_tag(github, bundle.tag, bundle.source_sha)
    uploaded, _ = remote_inventory(github, release_id, bundle, draft=False)
    require(uploaded == bundle.assets.keys(), "Release inventory changed during public download verification")


def publish(github: GitHub, dispatch: Dispatch, bundle: Bundle) -> int:
    verify_tag(github, bundle.tag, bundle.source_sha)
    release = github.release(bundle.tag)
    if release is None:
        require(not dispatch.promote, "Publish and verify a prerelease before promotion")
        release = github.create_draft(bundle)
    release_id = release_id_for(release, bundle)

    if dispatch.promote:
        require(not release["draft"], "Publish and verify a prerelease before promotion")
        reports = validate_provisioning(bundle, dispatch.provisioning_sha256, release_id)
        marker = f"<!-- desktop-provisioning-evidence-sha256={dispatch.provisioning_sha256} -->"
        if not release["prerelease"]:
            require(marker in release["body"].splitlines(), "Release already promoted with different evidence")
        verify_public(github, release_id, bundle)
        if release["prerelease"]:
            links = "\n".join(
                f"- `{target}`: [downloaded installer acceptance]({url})" for target, url in sorted(reports.items())
            )
            verify_tag(github, bundle.tag, bundle.source_sha)
            updated = github.update(release_id, prerelease=False, body=f"{release['body']}\n{marker}\n\n{links}\n")
            require(
                release_id_for(updated, bundle) == release_id and not updated["draft"] and not updated["prerelease"],
                "GitHub did not promote the approved prerelease",
            )
    else:
        if release["draft"]:
            require(release["prerelease"], "Refusing to publish a draft without the prerelease flag")
            stage_draft(github, release_id, bundle)
            verify_tag(github, bundle.tag, bundle.source_sha)
            updated = github.update(release_id, draft=False, prerelease=True)
            require(
                release_id_for(updated, bundle) == release_id and not updated["draft"] and updated["prerelease"],
                "GitHub did not publish the approved prerelease",
            )
        verify_public(github, release_id, bundle)
    return release_id


def inspect(github: GitHub, dispatch: Dispatch, bundle: Bundle) -> None:
    lines = [
        f"Desktop {dispatch.operation}: `{bundle.tag}`",
        f"- Desktop source: `{bundle.source_sha}`",
        f"- Server source: `{bundle.server_source_sha}`",
        f"- Candidate manifest: `{bundle.candidate_sha256}`",
        f"- Release manifest: `{bundle.manifest_sha256}`",
    ]
    for target, report in sorted(bundle.qualification_reports.items()):
        lines.append(
            f"- `{target}`: [platform acceptance]({report}) · [Server qualification]({bundle.server_reports[target]})"
        )
        lines.extend(f"  - `{asset.name}`: `{asset.sha256}`" for asset in bundle.targets[target].values())
    if dispatch.promote:
        release = github.release(bundle.tag)
        if release is None or release["draft"]:
            raise PublicationError("Promotion needs a public prerelease")  # noqa: TRY003
        for target, report in sorted(
            validate_provisioning(bundle, dispatch.provisioning_sha256, release_id_for(release, bundle)).items()
        ):
            lines.append(f"- `{target}`: [downloaded installer acceptance]({report})")
        lines.append(f"- Downloaded provisioning evidence: `{dispatch.provisioning_sha256}`")
    with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a", encoding="utf-8") as output:
        output.write("\n".join(lines) + "\n")


def raw_archive(directory: Path) -> Path:
    paths = list(directory.iterdir())
    require(len(paths) == 1 and paths[0].is_file(), f"Expected one immutable ZIP in {directory.name}")
    return paths[0]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("step", choices=["gate", "inspect", "publish"])
    step = parser.parse_args().step
    try:
        dispatch = Dispatch.current()
        github = GitHub(os.environ["GH_TOKEN"])
        if step == "gate":
            gate(github, dispatch)
            with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
                output.write(
                    f"run_id={dispatch.run_id}\nartifact_id={dispatch.artifact_id}\n"
                    f"candidate_run_id={dispatch.candidate_run_id}\n"
                    f"candidate_artifact_id={dispatch.candidate_artifact_id}\n"
                )
            return
        temp = Path(os.environ["RUNNER_TEMP"])
        with tempfile.TemporaryDirectory(prefix="desktop-release-staged-", dir=temp) as directory:
            root = Path(directory)
            extract_stage(raw_archive(temp / "desktop-staging"), root)
            require(not (root / CANDIDATE).exists(), "Signing handoff must not supply a candidate manifest")
            extract_stage(raw_archive(temp / "desktop-candidate"), root, only=CANDIDATE)
            bundle = validate_bundle(
                root,
                dispatch.tag,
                dispatch.source_sha,
                dispatch.manifest_sha256,
                candidate_run_id=dispatch.candidate_run_id,
                candidate_artifact_id=dispatch.candidate_artifact_id,
                promote=dispatch.promote,
            )
            if step == "inspect":
                inspect(github, dispatch, bundle)
                return
            # Recheck even after environment approval, before any authenticated write.
            gate(github, dispatch)
            release_id = publish(github, dispatch, bundle)
            print(f"Verified public assets for {bundle.tag} (Release {release_id})")
    except PublicationError as error:
        raise SystemExit(str(error)) from None
    except (BadZipFile, KeyError, ValueError, AttributeError, TypeError, OSError, URLError) as error:
        # Never echo staged JSON, server response bodies, or signed redirect URLs.
        raise SystemExit(f"Desktop publication could not complete: {type(error).__name__}") from None  # noqa: TRY003


if __name__ == "__main__":
    main()
