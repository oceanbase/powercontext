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
'use strict';

(function () {
  const element = (id) => document.getElementById(id);
  let savedUrl = '';
  let credentialSaved = false;
  let loading = true;

  async function checkedFetch(url, options) {
    const response = await fetch(url, options);
    if (!response.ok) throw new Error('Host settings operation failed. Retry from plugin settings.');
    return response;
  }

  function normalizedUrl() {
    const url = new URL(element('server-url').value.trim());
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash) {
      throw new Error('Use an HTTP(S) Server URL without credentials, query parameters, or a fragment.');
    }
    return url.href.replace(/\/+$/, '').replace(/\/mcp$/, '').replace(/\/+$/, '');
  }

  async function refreshCredential() {
    const secrets = await (await checkedFetch('/secrets')).json();
    credentialSaved = Array.isArray(secrets) && secrets.some((item) => item.key === 'server_credential' && item.saved);
    element('credential-status').textContent = credentialSaved ? 'Credential saved in Cindy.' : 'No saved credential.';
  }

  async function load() {
    try {
      const config = await (await checkedFetch('/kv')).json();
      element('server-url').value = config.server_url ?? 'http://127.0.0.1:8000';
      savedUrl = normalizedUrl();
      element('scope-id').value = config.scope_id ?? '';
      element('auth-mode').value = config.auth_mode ?? 'none';
      element('allow-insecure').checked = config.allow_insecure_http === true;
      await refreshCredential();
      loading = false;
    } catch {
      element('status').textContent = 'Unable to load settings. Reopen this page before saving.';
    }
  }

  element('server-url').addEventListener('input', () => {
    // Consent for one address must not silently follow a different address.
    element('allow-insecure').checked = false;
  });

  element('settings').addEventListener('submit', async (event) => {
    event.preventDefault();
    if (loading) return;
    loading = true;
    try {
      const serverUrl = normalizedUrl();
      const authMode = element('auth-mode').value;
      const allowInsecure = element('allow-insecure').checked;
      const host = new URL(serverUrl).hostname.replace(/^\[|\]$/g, '');
      const loopback = host === 'localhost' || host === '::1' || /^127(?:\.\d{1,3}){3}$/.test(host);
      if (serverUrl.startsWith('http:') && !loopback && !allowInsecure) {
        throw new Error('Use HTTPS or explicitly allow plaintext HTTP for this address.');
      }
      const token = element('token').value.trim().replace(/^Bearer /i, '');
      if (authMode === 'bearer' && (!credentialSaved || serverUrl !== savedUrl) && !token) {
        throw new Error('Enter a Bearer token for this Server URL.');
      }
      if (token) {
        if (authMode !== 'bearer') throw new Error('Select Bearer authentication before saving a token.');
        if (!/^[\x21-\x7e]+$/.test(token) || token.toLowerCase() === 'bearer') throw new Error('Invalid Bearer token.');
        // The URL travels inside safeStorage with the token, so partial saves and later
        // configuration changes cannot send an old credential to a different Server.
        await checkedFetch('/secrets/server_credential', {
          method: 'PUT', body: JSON.stringify({ value: JSON.stringify({ server_url: serverUrl, authorization: token }) }),
        });
      }
      const config = { server_url: serverUrl, auth_mode: authMode, allow_insecure_http: allowInsecure };
      const scopeId = element('scope-id').value.trim();
      if (scopeId) config.scope_id = scopeId;
      await checkedFetch('/kv', { method: 'PUT', body: JSON.stringify(config) });
      savedUrl = serverUrl;
      element('server-url').value = serverUrl;
      element('status').textContent = 'Settings saved. Call the status tool to verify the connection.';
      await refreshCredential();
    } catch (error) {
      // URL parser errors can contain the original URL; show only our fixed messages.
      element('status').textContent = error instanceof TypeError ? 'Invalid Server URL.' : error.message;
    } finally {
      element('token').value = '';
      loading = false;
    }
  });

  element('clear-token').addEventListener('click', async () => {
    if (loading) return;
    loading = true;
    try {
      await checkedFetch('/secrets/server_credential', { method: 'DELETE' });
      element('token').value = '';
      await refreshCredential();
      element('status').textContent = 'Credential removed. Bearer requests remain disabled until a new token is saved.';
    } catch {
      element('status').textContent = 'Unable to remove credential. Retry from plugin settings.';
    } finally { loading = false; }
  });

  void load();
})();
