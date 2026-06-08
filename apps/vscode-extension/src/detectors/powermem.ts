/**
 * PowerMem backend detection (health + optional info)
 * Uses Node.js http/https module for maximum compatibility across
 * VS Code, Cursor, CodeFuse, and other VS Code-based IDEs.
 */

import * as http from 'http';
import * as https from 'https';

function httpGet(url: string, timeoutMs = 3000): Promise<{ ok: boolean; body: string }> {
  return new Promise((resolve) => {
    const parsed = new URL(url);
    const mod = parsed.protocol === 'https:' ? https : http;
    const req = mod.request(
      {
        hostname: parsed.hostname,
        port: parsed.port || (parsed.protocol === 'https:' ? 443 : 80),
        path: parsed.pathname + parsed.search,
        method: 'GET',
        timeout: timeoutMs,
      },
      (res) => {
        const chunks: Buffer[] = [];
        res.on('data', (chunk: Buffer) => chunks.push(chunk));
        res.on('end', () => {
          const body = Buffer.concat(chunks).toString('utf-8');
          resolve({ ok: res.statusCode !== undefined && res.statusCode >= 200 && res.statusCode < 300, body });
        });
      }
    );
    req.on('error', () => resolve({ ok: false, body: '' }));
    req.on('timeout', () => { req.destroy(); resolve({ ok: false, body: '' }); });
    req.end();
  });
}

export async function detectBackend(url: string): Promise<boolean> {
  const base = url.replace(/\/+$/, '');
  try {
    const { ok } = await httpGet(`${base}/api/v1/system/health`, 3000);
    return ok;
  } catch {
    return false;
  }
}

export async function getBackendInfo(url: string): Promise<{ status?: string } | null> {
  try {
    const base = url.replace(/\/+$/, '');
    const { ok, body } = await httpGet(`${base}/api/v1/system/health`, 5000);
    if (!ok) return null;
    const json = JSON.parse(body) as { data?: { status?: string } };
    return json?.data ? { status: json.data.status } : null;
  } catch {
    return null;
  }
}
