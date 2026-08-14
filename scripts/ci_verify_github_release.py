"""Verify GitHub Release state and required PowerContext assets."""

from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


class ReleaseVerificationError(RuntimeError):
    """Report an invalid GitHub Release or missing asset."""


def fetch_release(repository: str, tag: str, token: str | None = None) -> dict[str, Any]:
    """Read one GitHub Release by tag."""

    quoted_tag = urllib.parse.quote(tag, safe="")
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repository}/releases/tags/{quoted_tag}",
        headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            **({"Authorization": f"Bearer {token}"} if token else {}),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - fixed GitHub API origin
            return json.load(response)
    except urllib.error.HTTPError as error:
        raise ReleaseVerificationError(  # noqa: TRY003 - command diagnostics include the exact Release
            f"GitHub returned HTTP {error.code} for Release {repository}@{tag}"
        ) from error
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
        raise ReleaseVerificationError(  # noqa: TRY003 - command diagnostics include the upstream failure
            f"Could not read GitHub Release {repository}@{tag}: {error}"
        ) from error


def validate_release(metadata: dict[str, Any], tag: str, expected_assets: set[str]) -> tuple[str, ...]:
    """Validate Release identity, publication state, and exact required assets."""

    if metadata.get("tag_name") != tag:
        raise ReleaseVerificationError(  # noqa: TRY003 - command diagnostics include expected and actual tags
            f"GitHub Release tag mismatch: expected {tag}, received {metadata.get('tag_name')!r}"
        )
    if metadata.get("draft") is not False:
        raise ReleaseVerificationError(f"GitHub Release {tag} is still a draft")  # noqa: TRY003
    assets = metadata.get("assets")
    if not isinstance(assets, list):
        raise ReleaseVerificationError(f"GitHub Release {tag} returned no asset list")  # noqa: TRY003
    asset_names = {str(item["name"]) for item in assets if isinstance(item, dict) and "name" in item}
    missing = sorted(expected_assets - asset_names)
    if missing:
        available = ", ".join(sorted(asset_names)) or "none"
        raise ReleaseVerificationError(  # noqa: TRY003 - command diagnostics enumerate missing assets
            f"GitHub Release {tag} is missing assets: {', '.join(missing)}; available: {available}"
        )
    return tuple(sorted(asset_names))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--expected-asset", action="append", required=True)
    args = parser.parse_args()

    try:
        metadata = fetch_release(args.repository, args.tag, os.environ.get("GITHUB_TOKEN"))
        assets = validate_release(metadata, args.tag, set(args.expected_asset))
    except ReleaseVerificationError as error:
        raise SystemExit(str(error)) from error
    print(f"Verified GitHub Release {args.repository}@{args.tag}:")
    for asset in assets:
        print(f"- {asset}")


if __name__ == "__main__":
    main()
