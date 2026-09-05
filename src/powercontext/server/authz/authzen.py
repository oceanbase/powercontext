# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Decision-only OpenID AuthZEN Authorization API 1.0 adapter."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, TypeGuard

import httpx
from pydantic import SecretStr

from powercontext.limits import MAX_POLICY_REVISION_LENGTH
from powercontext.server.authz.errors import AccessUnavailableError
from powercontext.server.authz.models import AccessDecision, AccessSubjectRef, MemoryEntrySelector, ResourceRef
from powercontext.server.authz.service import (
    AccessRequest,
    AuthorizedResourceFilter,
    ResourceSearchRequest,
)
from powercontext.transport import is_plaintext_non_loopback

_EVALUATION_PATH = "/access/v1/evaluation"
_EVALUATIONS_PATH = "/access/v1/evaluations"


class AuthZenAuthorizationProvider:
    """Call an AuthZEN PDP without claiming relationship or search capabilities.

    Only the standard decision boolean and a bounded optional ``policy_revision`` context value
    cross back into PowerContext. Provider response bodies, URLs, rules, obligations, and errors
    never become public reason codes or Access Audit fields.
    """

    def __init__(
        self,
        base_url: str,
        *,
        token: SecretStr | None = None,
        timeout: float = 10.0,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        normalized = _authzen_base_url(base_url)
        self._base_url = normalized
        self._headers = None if token is None else {"Authorization": f"Bearer {token.get_secret_value()}"}
        self._owned_client = None if http_client is not None else httpx.AsyncClient(timeout=timeout)
        resolved_client = http_client or self._owned_client
        if resolved_client is None:
            raise AccessUnavailableError  # pragma: no cover - construction guarantees a client.
        self._client: httpx.AsyncClient = resolved_client

    async def __aenter__(self) -> AuthZenAuthorizationProvider:
        return self

    async def __aexit__(self, *_exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owned_client is not None:
            await self._owned_client.aclose()

    async def check(self, request: AccessRequest, /) -> AccessDecision:
        payload = await self._post(_EVALUATION_PATH, _access_request(request))
        return _decision(payload)

    async def check_batch(
        self,
        requests: Sequence[AccessRequest],
        /,
    ) -> tuple[AccessDecision, ...]:
        if not requests:
            return ()
        payload = await self._post(
            _EVALUATIONS_PATH,
            {
                "evaluations": [_access_request(request) for request in requests],
                "options": {"evaluations_semantic": "execute_all"},
            },
        )
        values = payload.get("evaluations")
        if not isinstance(values, list) or len(values) != len(requests):
            raise AccessUnavailableError
        return tuple(_decision(value) for value in values)

    async def resolve_resource_filter(
        self,
        request: ResourceSearchRequest,
        /,
    ) -> AuthorizedResourceFilter:
        del request
        raise AccessUnavailableError("safe_resource_filtering_unavailable")

    async def _post(self, path: str, payload: Mapping[str, object]) -> Mapping[str, Any]:
        try:
            response = await self._client.post(f"{self._base_url}{path}", headers=self._headers, json=payload)
            response.raise_for_status()
            value = response.json()
        except (httpx.HTTPError, ValueError, TypeError) as error:
            raise AccessUnavailableError from error
        if not isinstance(value, Mapping):
            raise AccessUnavailableError
        return value


def _access_request(request: AccessRequest) -> dict[str, object]:
    return {
        "subject": _subject(request.subject),
        "action": {"name": request.action.value},
        "resource": _resource(request.resource),
        "context": {
            "request_id": request.context.request_id,
            "transport": request.context.transport,
            "operation": request.context.operation,
            "powercontext": {
                "actor": None if request.context.actor is None else _subject(request.context.actor),
                "subject_groups": [_subject(group) for group in request.context.subject_groups],
            },
        },
    }


def _subject(subject: AccessSubjectRef) -> dict[str, object]:
    return {
        "type": subject.type,
        "id": subject.id,
        "properties": {} if subject.description is None else {"description": subject.description},
    }


def _resource(resource: ResourceRef) -> dict[str, object]:
    properties: dict[str, object] = {}
    if resource.deployment_id is not None:
        properties["deployment_id"] = resource.deployment_id
    if resource.scope_id is not None:
        properties["scope_id"] = resource.scope_id
    if resource.identity is not None:
        properties["identity"] = {
            "family": resource.identity.family,
            "artifact_id": resource.identity.artifact_id,
        }
    if resource.selector is not None:
        properties["selector"] = _selector(resource.selector)
    return {"type": resource.type.value, "id": resource.key, "properties": properties}


def _selector(selector: MemoryEntrySelector) -> dict[str, str]:
    return {
        "type": selector.type,
        "entry_id": selector.entry_id,
    }


def _decision(value: object) -> AccessDecision:
    if not isinstance(value, Mapping):
        raise AccessUnavailableError
    decision = value.get("decision")
    if type(decision) is not bool:
        raise AccessUnavailableError
    allowed = decision
    context = value.get("context")
    policy_revision: str | None = None
    if isinstance(context, Mapping):
        candidate = context.get("policy_revision")
        if candidate is not None:
            if not _valid_policy_revision(candidate):
                raise AccessUnavailableError
            policy_revision = candidate
    return AccessDecision(
        allowed=allowed,
        reason_code="authzen-allow" if allowed else "authzen-deny",
        policy_revision=policy_revision,
    )


def _valid_policy_revision(value: object) -> TypeGuard[str]:
    return (
        isinstance(value, str)
        and 0 < len(value) <= MAX_POLICY_REVISION_LENGTH
        and value[0].isalnum()
        and all(character.isascii() and (character.isalnum() or character in "._-") for character in value)
    )


def _authzen_base_url(value: str) -> str:
    url = httpx.URL(value)
    if (
        url.scheme not in {"http", "https"}
        or not url.host
        or url.userinfo
        or url.query
        or url.fragment
        or is_plaintext_non_loopback(str(url))
    ):
        raise ValueError("AuthZEN base URL must be credential-free HTTPS or loopback HTTP")  # noqa: TRY003
    return str(url).rstrip("/")


__all__ = ("AuthZenAuthorizationProvider",)
