/*
Copyright (c) 2026 OceanBase.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
*/
(() => {
  const instances = new Map();
  const closing = new WeakMap();

  function closeDrawer(root) {
    const drawer = root.querySelector('[data-processing-drawer]');
    if (drawer && closing.has(drawer)) return closing.get(drawer);
    const instance = drawer && tabler.Offcanvas.getInstance(drawer);
    if (!instance) return Promise.resolve();
    if (!drawer.hasAttribute('aria-modal')) {
      instance.dispose();
      return Promise.resolve();
    }
    const closed = new Promise(resolve => {
      drawer.addEventListener('hidden.bs.offcanvas', () => {
        instance.dispose();
        closing.delete(drawer);
        resolve();
      }, {once: true});
      instance.hide();
    });
    closing.set(drawer, closed);
    return closed;
  }

  function expire(root, state, expiresAt = 0) {
    clearTimeout(state.timer);
    const snapshot = root.querySelector('[data-processing-snapshot]');
    if (!snapshot?.dataset.expires) return;
    const remaining = expiresAt - performance.now();
    if (remaining > 0) {
      state.timer = setTimeout(() => expire(root, state, expiresAt), remaining);
      return;
    }
    root.querySelectorAll('[data-processing-executor]').forEach(node => {
      node.textContent = root.dataset.stale;
    });
  }

  async function load(root, state) {
    state.controller?.abort();
    const controller = new AbortController();
    state.controller = controller;
    const feedback = root.querySelector('[data-processing-feedback]');
    const result = root.querySelector('[data-processing-result]');
    const refresh = root.querySelector('[data-processing-refresh]');
    refresh.disabled = true;
    root.setAttribute('aria-busy', 'true');
    const requestedAt = performance.now();
    const deadline = setTimeout(() => controller.abort(), 3500);
    try {
      const response = await fetch(root.dataset.url, {
        credentials: 'same-origin', cache: 'no-store', signal: controller.signal,
      });
      if ([401, 403, 404].includes(response.status)) {
        await closeDrawer(root);
        if (!root.isConnected || state.controller !== controller || state.suspended) return;
        result.replaceChildren();
        feedback.textContent = root.dataset.denied;
        return;
      }
      if (!response.ok) throw new Error('unavailable');
      const content = document.createElement('template');
      content.innerHTML = await response.text();
      const snapshot = content.content.querySelector('[data-processing-snapshot]');
      if (!snapshot || snapshot.dataset.scope !== root.dataset.scope || snapshot.dataset.family !== root.dataset.family) {
        throw new Error('foreign_snapshot');
      }
      if (!root.isConnected || state.controller !== controller || state.suspended) return;
      await closeDrawer(root);
      if (!root.isConnected || state.controller !== controller || state.suspended) return;
      result.replaceChildren(content.content);
      feedback.textContent = '';
      const button = root.querySelector('[data-processing-details]');
      const drawer = root.querySelector('[data-processing-drawer]');
      button.addEventListener('click', () => {
        button.setAttribute('aria-expanded', 'true');
        tabler.Offcanvas.getOrCreateInstance(drawer).show();
      });
      drawer.addEventListener('hidden.bs.offcanvas', () => {
        button.setAttribute('aria-expanded', 'false');
        if (button.isConnected && !state.suspended) button.focus();
      });
      // Using request start conservatively includes time spent fetching and rendering the snapshot.
      const lifetime = Date.parse(snapshot.dataset.expires) - Date.parse(snapshot.dataset.queried);
      expire(root, state, requestedAt + lifetime);
    } catch {
      if (!root.isConnected || state.controller !== controller || state.suspended) return;
      feedback.textContent = root.dataset.error + (result.childElementCount ? ' ' + root.dataset.previous : '');
    } finally {
      clearTimeout(deadline);
      if (root.isConnected && state.controller === controller && !state.suspended) {
        refresh.disabled = false;
        root.removeAttribute('aria-busy');
      }
    }
  }

  function mount() {
    for (const [root, state] of instances) {
      if (!root.isConnected) {
        state.controller?.abort();
        clearTimeout(state.timer);
        void closeDrawer(root);
        instances.delete(root);
      }
    }
    document.querySelectorAll('[data-artifact-processing]').forEach(root => {
      if (instances.has(root)) return;
      const state = {controller: null, timer: null, suspended: false};
      instances.set(root, state);
      root.querySelector('[data-processing-refresh]').addEventListener('click', () => load(root, state));
      expire(root, state);
      void load(root, state);
    });
  }

  document.addEventListener('htmx:beforeRequest', event => {
    const path = event.detail.pathInfo?.finalRequestPath || event.detail.pathInfo?.requestPath;
    if (!path) return;
    const url = new URL(path, location.href);
    for (const [root, state] of instances) {
      if (!event.detail.target?.contains(root)) continue;
      const scope = url.searchParams.get('scope');
      const family = url.pathname === '/dashboard/methods' ? url.searchParams.get('kind') || 'experience' : null;
      const scopeChanged = url.searchParams.has('scope') && scope !== root.dataset.scope;
      if (url.pathname !== location.pathname || scopeChanged || (family && family !== root.dataset.family)) {
        state.suspended = true;
        root.querySelector('[data-processing-refresh]').disabled = true;
        const navigation = event.detail.xhr;
        state.navigation = navigation;
        state.controller?.abort();
        void closeDrawer(root).then(() => {
          if (state.suspended && state.navigation === navigation) {
            root.querySelector('[data-processing-result]')?.replaceChildren();
          }
        });
      }
    }
  });
  document.addEventListener('htmx:afterRequest', event => {
    const {failed, xhr} = event.detail;
    // HTMX sets failed for HTTP errors. A transport failure ends with XHR status 0.
    if (!failed && xhr.status !== 0 && xhr.status !== 204) return;
    for (const [root, state] of instances) {
      if (!root.isConnected || !state.suspended || state.navigation !== xhr) continue;
      state.navigation = null;
      state.suspended = false;
      void load(root, state);
    }
  });
  document.addEventListener('htmx:beforeCleanupElement', event => {
    for (const [root, state] of instances) {
      if (event.detail.elt !== root && !event.detail.elt.contains(root)) continue;
      state.suspended = true;
      state.controller?.abort();
      clearTimeout(state.timer);
      void closeDrawer(root);
      instances.delete(root);
    }
  });
  document.addEventListener('htmx:beforeHistorySave', () => {
    // Cached pages must not restore an open drawer or a process-local execution claim.
    for (const [root, state] of instances) {
      state.suspended = true;
      state.controller?.abort();
      clearTimeout(state.timer);
      void closeDrawer(root);
      root.querySelector('[data-processing-result]')?.replaceChildren();
    }
  });
  document.addEventListener('htmx:load', mount);
  document.addEventListener('DOMContentLoaded', mount);
})();
