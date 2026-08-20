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

const tokenKey = "powercontext.server.token";

function setSessionState(state) {
  document.documentElement.dataset.serverSession = state;
}

export function readServerToken() {
  try {
    return sessionStorage.getItem(tokenKey);
  } catch (error) {
    return null;
  }
}

export function storeServerToken(token) {
  setSessionState("active");
  try {
    sessionStorage.setItem(tokenKey, token);
  } catch (error) {
    // Authentication still applies to the current request when storage is unavailable.
  }
}

export function clearServerToken() {
  setSessionState("missing");
  try {
    sessionStorage.removeItem(tokenKey);
  } catch (error) {
    // The current page can still return to its signed-out state.
  }
}

export function fetchWithBearer(resource, token, options = {}) {
  const headers = new Headers(options.headers);
  headers.set("Authorization", `Bearer ${token}`);
  return fetch(resource, {...options, headers, cache: options.cache || "no-store"});
}
