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
export const contractSha256 = "6ac34926b9c7f4fa975512833ca026ec43da9073e92b53068e2a987b730f2bb8";
export const operations = {
  "get_liveness": {
    "method": "GET",
    "path": "/health/live"
  },
  "get_readiness": {
    "method": "GET",
    "path": "/health/ready"
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
  "get_artifact_revision": {
    "method": "GET",
    "path": "/v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}/revisions/{revision}"
  },
  "get_access_principal": {
    "method": "GET",
    "path": "/v1/access/me"
  }
} as const;
