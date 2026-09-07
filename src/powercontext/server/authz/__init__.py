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

"""Server-owned authentication and authorization building blocks."""

from powercontext.server.authz.authzen import AuthZenAuthorizationProvider
from powercontext.server.authz.casbin import CasbinAuthorizationProvider
from powercontext.server.authz.errors import (
    AccessBindingNotFoundError,
    AccessConflictError,
    AccessControlError,
    AccessDeniedError,
    AccessIdentityRequiredError,
    AccessInvalidRequestError,
    AccessUnavailableError,
)
from powercontext.server.authz.models import (
    DEFAULT_DEPLOYMENT_ID,
    PUBLIC_ACCESS_ACTIONS,
    ROLE_CARDINALITIES,
    AccessAction,
    AccessAuditEvent,
    AccessBinding,
    AccessBindingState,
    AccessDecision,
    AccessResourceType,
    AccessRole,
    AccessRoleCardinality,
    AccessSubjectRef,
    ArtifactIdentity,
    ArtifactOwnerRelation,
    CandidateOwnerAttestation,
    GroupRef,
    HandoffReceiptIdentity,
    MemoryEntrySelector,
    PrincipalRef,
    ResourceRef,
)
from powercontext.server.authz.service import (
    AccessAuditContext,
    AccessAuditPage,
    AccessAuditStore,
    AccessBindingPage,
    AccessControlService,
    AccessProviderCapabilities,
    AccessRequest,
    AuditSearchRequest,
    AuthorizationProvider,
    AuthorizedResourceFilter,
    AuthorizedResourcePage,
    BindingReplacement,
    BindingSearchRequest,
    BuiltinAuthorizationProvider,
    CreateBinding,
    RelationshipReader,
    RelationshipStore,
    RelationshipWriter,
    ReplaceBinding,
    ResourceSearchRequest,
    access_control_for_mode,
)

__all__ = (
    "DEFAULT_DEPLOYMENT_ID",
    "PUBLIC_ACCESS_ACTIONS",
    "ROLE_CARDINALITIES",
    "AccessAction",
    "AccessAuditContext",
    "AccessAuditEvent",
    "AccessAuditPage",
    "AccessAuditStore",
    "AccessBinding",
    "AccessBindingNotFoundError",
    "AccessBindingPage",
    "AccessBindingState",
    "AccessConflictError",
    "AccessControlError",
    "AccessControlService",
    "AccessDecision",
    "AccessDeniedError",
    "AccessIdentityRequiredError",
    "AccessInvalidRequestError",
    "AccessProviderCapabilities",
    "AccessRequest",
    "AccessResourceType",
    "AccessRole",
    "AccessRoleCardinality",
    "AccessSubjectRef",
    "AccessUnavailableError",
    "ArtifactIdentity",
    "ArtifactOwnerRelation",
    "AuditSearchRequest",
    "AuthZenAuthorizationProvider",
    "AuthorizationProvider",
    "AuthorizedResourceFilter",
    "AuthorizedResourcePage",
    "BindingReplacement",
    "BindingSearchRequest",
    "BuiltinAuthorizationProvider",
    "CandidateOwnerAttestation",
    "CasbinAuthorizationProvider",
    "CreateBinding",
    "GroupRef",
    "HandoffReceiptIdentity",
    "MemoryEntrySelector",
    "PrincipalRef",
    "RelationshipReader",
    "RelationshipStore",
    "RelationshipWriter",
    "ReplaceBinding",
    "ResourceRef",
    "ResourceSearchRequest",
    "access_control_for_mode",
)
