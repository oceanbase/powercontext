/*
 * Copyright (c) 2026 OceanBase.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

// Generated from openapi/powercontext.yaml. Do not edit.
export const contractSha256 = "91b9762f8d2f9e97e48a15c7155a3b114ec9ec8d707432b2dbb227c4e12f74b2";
export const operations = {
  "get_liveness": {
    "method": "GET",
    "path": "/health/live"
  },
  "get_readiness": {
    "method": "GET",
    "path": "/health/ready"
  },
  "get_server_info": {
    "method": "GET",
    "path": "/v1/server-info"
  },
  "get_capabilities": {
    "method": "GET",
    "path": "/v1/capabilities"
  },
  "list_scopes": {
    "method": "GET",
    "path": "/v1/scopes"
  },
  "get_scope": {
    "method": "GET",
    "path": "/v1/scopes/{scope_id}"
  },
  "get_default_scope": {
    "method": "GET",
    "path": "/v1/scopes/default"
  },
  "remember_memory": {
    "method": "POST",
    "path": "/v1/memory/remember"
  },
  "search_memory": {
    "method": "POST",
    "path": "/v1/memory/search"
  },
  "get_stats": {
    "method": "POST",
    "path": "/v1/stats"
  },
  "get_artifact_revision": {
    "method": "GET",
    "path": "/v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}/revisions/{revision}"
  },
  "get_connection_identity": {
    "method": "GET",
    "path": "/v1/connection/identity"
  },
  "get_access_principal": {
    "method": "GET",
    "path": "/v1/access/me"
  }
} as const;
