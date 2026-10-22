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

"""Process identity shared by Server log and trace adapters."""

import re
from importlib.metadata import PackageNotFoundError, version
from urllib.parse import urlsplit
from uuid import uuid4

from opentelemetry.sdk.resources import SERVICE_NAME, Resource

_INSTANCE_ID = uuid4().hex


def service_resource(role: str = "server", *, target: str | None = None, owner_role: str | None = None) -> Resource:
    """Build the process identity shared by log and trace adapters.

    Source checkouts, vendored trees, and frozen bundles have no installed distribution
    metadata, so an unresolvable version omits the field instead of failing configuration.
    """

    resource = Resource.create()
    attributes = dict(resource.attributes)
    api_target = attributes.pop("powercontext.service.api_target", None)
    background_target = attributes.pop("powercontext.service.background_target", None)
    configured = attributes.pop("powercontext.service.target", None)
    attributes.pop("powercontext.service.owner_role", None)
    if target is None:
        selected = (
            background_target if role == "background" else api_target if role in {"all", "api", "server"} else None
        )
        target = _safe_target(selected if selected is not None else configured)
    else:
        target = _safe_target(target)
    resource = Resource(attributes, schema_url=resource.schema_url)
    if str(resource.attributes.get(SERVICE_NAME, "")).startswith("unknown_service"):
        resource = resource.merge(Resource({SERVICE_NAME: "powercontext-server"}))
    identity = {
        "service.instance.id": _INSTANCE_ID,
        "powercontext.service.role": role,
    }
    owner = owner_role if role == "worker" else role
    if owner in {"all", "api", "background", "server"}:
        identity["powercontext.service.owner_role"] = owner
    if target is not None:
        identity["powercontext.service.target"] = target
    installed_version = _installed_version()
    if installed_version is not None:
        identity["service.version"] = installed_version
    return resource.merge(Resource(identity))


def _safe_target(value: object) -> str | None:
    """Accept a configured scrape endpoint, excluding credentials, paths and arbitrary text."""
    if not isinstance(value, str) or len(value) > 255 or re.fullmatch(r'[^\s`"\\]+', value) is None:
        return None
    try:
        parsed = urlsplit("//" + value)
        if (
            parsed.hostname
            and parsed.port is not None
            and 1 <= parsed.port <= 65535
            and not any((parsed.username, parsed.password, parsed.path, parsed.query, parsed.fragment))
        ):
            return value
    except ValueError:
        pass
    return None


def _installed_version() -> str | None:
    try:
        return version("powercontext")
    except PackageNotFoundError:
        return None
