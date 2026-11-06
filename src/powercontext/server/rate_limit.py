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

"""Shared fixed-window admission for external HTTP requests.

The window lives in the deployment database rather than in process memory, so
every API replica charges the same principal budget. The middleware runs after
principal binding: without a bound principal it falls back to the client host,
which keeps an unauthenticated deployment from sharing one global window.
"""

from __future__ import annotations

import hashlib

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.rate_limit import RateLimitDecision, RateLimitRepository
from powercontext.builtin.runtime.config import RateLimitConfig
from powercontext.http import ErrorDetail, ErrorResponse
from powercontext.server.context import current_principal, is_internal_bridge

POLICY_ID = "server.http"

# Liveness, readiness and metrics are scraped on a fixed interval and must not
# consume a caller's budget.
_EXEMPT_PREFIXES = ("/health/live", "/health/ready", "/metrics")


class SharedRateLimitMiddleware:
    """Charge each external request to its principal's fixed window."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        config: RateLimitConfig,
        repository: RateLimitRepository | None = None,
    ) -> None:
        self.app = app
        self._config = config
        self._repository = RateLimitRepository() if repository is None else repository

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or is_internal_bridge():
            await self.app(scope, receive, send)
            return
        if scope.get("path", "").startswith(_EXEMPT_PREFIXES):
            await self.app(scope, receive, send)
            return
        database = _runtime_database(scope)
        if database is None:
            await self.app(scope, receive, send)
            return
        decision = await self._consume(database, _principal_key(scope))
        if not decision.allowed:
            response = JSONResponse(
                ErrorResponse(
                    error=ErrorDetail(
                        code="rate_limited",
                        message="The request budget for this principal is exhausted.",
                        details=None,
                    )
                ).model_dump(mode="json"),
                status_code=429,
                headers={"Retry-After": str(decision.retry_after_seconds)},
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)

    async def _consume(self, database: AsyncDatabase, principal_key: str) -> RateLimitDecision:
        async with database.transaction() as connection:
            return await self._repository.consume(
                connection,
                principal_key=principal_key,
                policy_id=POLICY_ID,
                limit=self._config.requests,
                window_seconds=self._config.window_seconds,
            )


def _runtime_database(scope: Scope) -> AsyncDatabase | None:
    """Return the composed runtime's database, or ``None`` outside the data plane."""

    app = scope.get("app")
    application = None if app is None else getattr(app.state, "application", None)
    if application is None:
        return None
    try:
        return application.primary_database
    except RuntimeError:
        # The lifespan has not opened the primary database yet (or already closed it).
        return None


def _principal_key(scope: Scope) -> str:
    """Derive the 64-character digest the repository requires as a window key."""

    principal = current_principal()
    if principal is not None:
        identity = f"{principal.type}:{principal.id}"
    else:
        client = scope.get("client")
        identity = f"anonymous:{client[0] if client else 'unknown'}"
    return hashlib.sha256(identity.encode()).hexdigest()


__all__ = ["POLICY_ID", "SharedRateLimitMiddleware"]
