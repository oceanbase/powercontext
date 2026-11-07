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

"""Resolve a fixed source release into immutable source wheels and backend cases."""

from __future__ import annotations

import hashlib
import json
import re
import urllib.request
from typing import Any
from urllib.parse import urlsplit

from packaging.version import Version

CATALOG_URL = "https://pypi.org/pypi/powercontext/json"
STABLE = r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"


def fetch_catalog() -> dict[str, Any]:
    # A discovery outage must fail closed, never reduce the matrix to zero cases.
    with urllib.request.urlopen(CATALOG_URL, timeout=30) as response:  # noqa: S310 -- fixed public PyPI endpoint
        return json.load(response)


def source_artifact(catalog: dict[str, Any], version: str) -> dict[str, str]:
    files = catalog["releases"].get(version, [])
    wheels = [
        item
        for item in files
        if item.get("filename") == f"powercontext-{version}-py3-none-any.whl"
        and item.get("packagetype") == "bdist_wheel"
        and not item.get("yanked", False)
    ]
    if len(wheels) != 1:
        raise ValueError(f"Required source {version} has no unique non-yanked universal wheel")  # noqa: TRY003
    wheel = wheels[0]
    url = urlsplit(wheel["url"])
    digest = wheel["digests"]["sha256"]
    if (
        url.scheme != "https"
        or url.netloc != "files.pythonhosted.org"
        or url.fragment
        or not re.fullmatch(r"[0-9a-f]{64}", digest)
    ):
        raise ValueError(f"Invalid source artifact metadata for {version}")  # noqa: TRY003
    return {"filename": wheel["filename"], "url": wheel["url"], "sha256": digest}


def resolve_matrix(policy: dict[str, Any], target: str) -> dict[str, Any]:
    automatic = policy["automatic_sources"]
    explicit = policy["cases"]
    if automatic is None and not explicit:
        raise ValueError("Upgrade acceptance requires a nonempty matrix")  # noqa: TRY003
    catalog = fetch_catalog()
    if not isinstance(catalog.get("releases"), dict) or not catalog["releases"]:
        raise ValueError("Empty or malformed PyPI release catalog")  # noqa: TRY003
    cases = [dict(case) for case in explicit]
    if automatic:
        cases.extend({"source_version": automatic["source_version"], **profile} for profile in automatic["profiles"])
    identities = set()
    for case in cases:
        version = case["source_version"]
        if Version(version) >= Version(target):
            raise ValueError(f"Source {version} must precede target {target}")  # noqa: TRY003
        identity = (version, case["backend"], case["configuration"])
        if identity in identities:
            raise ValueError(f"Duplicate resolved upgrade case: {identity}")  # noqa: TRY003
        identities.add(identity)
        case["source_artifact"] = source_artifact(catalog, version)
    return {
        "catalog_url": CATALOG_URL,
        "catalog_sha256": hashlib.sha256(json.dumps(catalog, sort_keys=True).encode()).hexdigest(),
        "candidate_cases": cases,
        "required_cases": cases,
    }
