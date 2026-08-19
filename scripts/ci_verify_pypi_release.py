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

"""Verify that one PowerContext release is available from the public PyPI API."""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

PYPI_RELEASE_URL = "https://pypi.org/pypi/{package}/{version}/json"
REQUIRED_PACKAGE_TYPES = frozenset({"bdist_wheel", "sdist"})


class ReleaseVerificationError(RuntimeError):
    """Report an unavailable or incomplete published package."""


def fetch_release(package: str, version: str) -> dict[str, Any]:
    """Read one exact release from PyPI."""

    url = PYPI_RELEASE_URL.format(package=package, version=version)
    try:
        with urllib.request.urlopen(url, timeout=30) as response:  # noqa: S310 - fixed PyPI origin
            return json.load(response)
    except urllib.error.HTTPError as error:
        raise ReleaseVerificationError(  # noqa: TRY003 - command diagnostics include the exact release
            f"PyPI returned HTTP {error.code} for {package}=={version}"
        ) from error
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
        raise ReleaseVerificationError(  # noqa: TRY003 - command diagnostics include the upstream failure
            f"Could not read PyPI metadata for {package}=={version}: {error}"
        ) from error


def validate_release(metadata: dict[str, Any], package: str, version: str) -> tuple[str, ...]:
    """Validate version metadata and required distribution types."""

    published_version = metadata.get("info", {}).get("version")
    if published_version != version:
        raise ReleaseVerificationError(  # noqa: TRY003 - command diagnostics include expected and actual versions
            f"PyPI metadata version mismatch for {package}: expected {version}, received {published_version!r}"
        )

    files = metadata.get("urls")
    if not isinstance(files, list):
        raise ReleaseVerificationError(  # noqa: TRY003 - command diagnostics include the exact release
            f"PyPI returned no distribution list for {package}=={version}"
        )
    package_types = {item.get("packagetype") for item in files if isinstance(item, dict)}
    missing = sorted(REQUIRED_PACKAGE_TYPES - package_types)
    if missing:
        available = ", ".join(sorted(value for value in package_types if isinstance(value, str))) or "none"
        raise ReleaseVerificationError(  # noqa: TRY003 - command diagnostics enumerate missing distributions
            f"PyPI release {package}=={version} is missing distribution types: {', '.join(missing)}; "
            f"available: {available}"
        )

    filenames = tuple(sorted(str(item["filename"]) for item in files if isinstance(item, dict) and "filename" in item))
    if not filenames:
        raise ReleaseVerificationError(  # noqa: TRY003 - command diagnostics include the exact release
            f"PyPI returned no distribution filenames for {package}=={version}"
        )
    return filenames


def verify_with_retry(
    package: str,
    version: str,
    *,
    attempts: int,
    delay_seconds: float,
    fetch: Callable[[str, str], dict[str, Any]] = fetch_release,
) -> tuple[str, ...]:
    """Retry verification while PyPI propagates a newly published release."""

    if attempts < 1:
        raise ValueError("attempts must be at least 1")  # noqa: TRY003
    last_error: ReleaseVerificationError | None = None
    for attempt in range(1, attempts + 1):
        try:
            return validate_release(fetch(package, version), package, version)
        except ReleaseVerificationError as error:
            last_error = error
            if attempt == attempts:
                break
            print(f"Attempt {attempt}/{attempts} failed: {error}. Retrying in {delay_seconds:g} seconds.")
            time.sleep(delay_seconds)
    if last_error is None:
        raise RuntimeError("verification retry loop completed without a result")  # noqa: TRY003
    raise last_error


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", default="powercontext")
    parser.add_argument("--version", required=True)
    parser.add_argument("--attempts", type=int, default=20)
    parser.add_argument("--delay-seconds", type=float, default=15)
    args = parser.parse_args()

    try:
        filenames = verify_with_retry(
            args.package,
            args.version,
            attempts=args.attempts,
            delay_seconds=args.delay_seconds,
        )
    except ReleaseVerificationError as error:
        raise SystemExit(str(error)) from error
    print(f"Verified PyPI release {args.package}=={args.version}:")
    for filename in filenames:
        print(f"- {filename}")


if __name__ == "__main__":
    main()
