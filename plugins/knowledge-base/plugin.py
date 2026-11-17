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

"""Install this directory as one PowerContext Artifact plugin."""

from pc_knowledge_base.models import (
    BatchChunks,
    DefaultUpdate,
    ImportFiles,
    KBCreate,
    Reprocess,
    Retrieve,
    RetryImport,
    Settings,
    SpaceCreate,
    SpaceUpdate,
)
from pc_knowledge_base.service import setup
from pc_knowledge_base.sources import Definition

from powercontext.plugins import ArtifactPlugin, Inputs, PluginApi, PluginPages, Route


def route(method, path, handler, model=None, *, write=False, binary=False):
    scoped = "{scope_id}" in path
    return Route(
        method=method,
        path=path,
        handler=handler,
        access=("scope.contribute" if write else "scope.read") if scoped else "authenticated",
        scope_param="scope_id" if scoped else None,
        body="binary" if binary else "json" if model else "none",
        request_model=model,
        max_body_bytes=16 * 1024 * 1024 if binary else None,
        timeout_seconds=180,
    )


C = "/scopes/{scope_id}/documents/{document_id}/revisions/{revision}/chunks"
plugin = ArtifactPlugin(
    family="document",
    setup=setup,
    on_sources=True,
    inputs=Inputs(source_types=("knowledge-base-process",), projection="knowledge-base.input@1", max_sources=10),
    definitions=(Definition("knowledge-base-file"), Definition("knowledge-base-process")),
    timeout_seconds=600,
    change_timeout_seconds=180,
    api=PluginApi(
        routes=(
            route("GET", "/spaces", "spaces_list"),
            route("POST", "/spaces", "spaces_create", SpaceCreate),
            route("PATCH", "/spaces/{space_id}", "spaces_update", SpaceUpdate),
            route("GET", "/me/default-space", "default_get"),
            route("PUT", "/me/default-space", "default_set", DefaultUpdate),
            route("GET", "/knowledge-bases", "kb_list"),
            route("POST", "/knowledge-bases", "kb_create", KBCreate),
            route("GET", "/scopes/{scope_id}/settings", "settings_get"),
            route("PATCH", "/scopes/{scope_id}/settings", "settings_update", Settings, write=True),
            route("POST", "/scopes/{scope_id}/imports", "imports", ImportFiles, write=True),
            route("POST", "/scopes/{scope_id}/uploads", "upload", write=True, binary=True),
            route("GET", "/scopes/{scope_id}/imports/{request_id}", "status"),
            route("POST", "/scopes/{scope_id}/imports/{request_id}/retry", "retry_import", RetryImport, write=True),
            route("GET", "/scopes/{scope_id}/documents", "documents"),
            route("POST", "/scopes/{scope_id}/documents/{document_id}/reprocess", "reprocess", Reprocess, write=True),
            route("GET", C, "chunk_read"),
            route("GET", C + "/{chunk_id}", "chunk_read"),
            route("POST", C + "/batch-get", "chunk_read", BatchChunks),
            route("POST", "/retrieve", "retrieve", Retrieve),
            route("POST", "/scopes/{scope_id}/retrieve", "retrieve", Retrieve),
        )
    ),
    pages=PluginPages(title={"en": "Knowledge base", "zh": "知识库"}),
)
