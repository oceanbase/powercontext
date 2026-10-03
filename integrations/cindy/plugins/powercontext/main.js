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
/* global cindy */
'use strict';

(function () {
  const diagnosticOutcomes = new Set(['authentication_failed', 'version_mismatch', 'server_unavailable', 'invalid_response']);

  async function diagnose(result, event) {
    if (!diagnosticOutcomes.has(result.code)) return;
    try {
      // Shared browser storage keeps the cooldown across on-demand invocations.
      const key = `powercontext.diagnostic.${result.code}`;
      const previous = Number(localStorage.getItem(key) ?? 0);
      if (Date.now() - previous < 60000) return;
      localStorage.setItem(key, String(Date.now()));
      const diagnostic = { component: 'powercontext.cindy', event, outcome: result.code };
      if (Number.isInteger(result.http_status)) diagnostic.http_status = result.http_status;
      if (result.code === 'server_unavailable') diagnostic.recovery = 'powercontext doctor';
      await cindy.send({ type: 'notify', tone: 'warning', text: JSON.stringify(diagnostic) });
    } catch { /* Diagnostics must never prevent completion of a tool call. */ }
  }

  cindy.onHostMessage(async function (msg) {
    if (msg.type !== 'tool-call') return;
    let result;
    try {
      if (msg.tool === 'read_manual') {
        const response = await fetch('/MANUAL.md');
        if (!response.ok) throw new Error('manual unavailable');
        result = { ok: true, data: { content: await response.text() } };
      } else {
        const response = await fetch('/kv');
        if (!response.ok) throw new Error('settings unavailable');
        const saved = await response.json();
        const config = {
          server_url: saved.server_url ?? 'http://127.0.0.1:8000',
          auth_mode: saved.auth_mode ?? 'none',
          allow_insecure_http: saved.allow_insecure_http ?? false,
          ...(saved.scope_id ? { scope_id: saved.scope_id } : {}),
        };
        // Host replaces this field before dispatch. Agent-supplied connection/config fields
        // remain ordinary arguments and are rejected by the worker's tool schema.
        const { session_context, ...args } = msg.args ?? {};
        const workerResponse = await cindy.node.request({
          method: config.auth_mode === 'bearer' ? 'powercontext/authenticated' : 'powercontext/request',
          params: { tool: msg.tool, args, config, session_context },
          timeoutMs: 15000,
        });
        result = workerResponse.ok && workerResponse.result && typeof workerResponse.result.ok === 'boolean'
          ? workerResponse.result
          : { ok: false, code: 'worker_unavailable', message: 'PowerContext worker unavailable. Check plugin settings.' };
      }
    } catch {
      result = { ok: false, code: 'plugin_unavailable', message: 'PowerContext plugin unavailable. Continue the task.' };
    }
    if (!result.ok && ['worker_unavailable', 'plugin_unavailable'].includes(result.code)
      && ['create_scope', 'bind_scope', 'remember_memory', 'capture_content_source'].includes(msg.tool)) {
      result.write_status = 'unknown';
    }
    if (!result.ok) await diagnose(result, msg.tool);
    await cindy.send(result.ok
      ? { type: 'tool-result', callId: msg.callId, ok: true, result: result.data }
      : { type: 'tool-result', callId: msg.callId, ok: false, errorCode: result.code,
        message: result.write_status === 'unknown'
          ? 'PowerContext write outcome is unknown. Check the Server before retrying.' : result.message });
  });
})();
