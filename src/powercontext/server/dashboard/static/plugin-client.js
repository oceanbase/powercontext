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

// The client a plugin page uses to call its plugin's routes. The page runs in a sandboxed
// frame without the Dashboard session; the Dashboard host page forwards each request.
(function () {
  "use strict";
  let nextId = 0;
  const pending = new Map();
  let resolveContext;
  const context = new Promise(resolve => { resolveContext = resolve; });

  window.addEventListener("message", event => {
    if (event.source !== window.parent) return;
    const data = event.data || {};
    if (data.type === "powercontext.context") {
      resolveContext(data.context);
    } else if (data.type === "powercontext.response" && pending.has(data.id)) {
      const settle = pending.get(data.id);
      pending.delete(data.id);
      settle(data);
    }
  });
  window.parent.postMessage({ type: "powercontext.ready" }, "*");

  function request(method, path, options = {}) {
    const id = ++nextId;
    const message = {
      type: "powercontext.request",
      id,
      method,
      path,
      query: options.query || {},
      json: options.json,
      body: options.body,
      contentType: options.contentType,
    };
    return new Promise((resolve, reject) => {
      pending.set(id, reply => {
        if (reply.error) {
          reject(Object.assign(new Error(reply.error), { status: reply.status }));
          return;
        }
        resolve({
          status: reply.status,
          ok: reply.status >= 200 && reply.status < 300,
          contentType: reply.contentType || "",
          filename: reply.filename || null,
          json: reply.json,
          blob: reply.blob,
        });
      });
      window.parent.postMessage(message, "*");
    });
  }

  window.powercontextPluginClient = function () {
    return { context: () => context, request };
  };
})();
