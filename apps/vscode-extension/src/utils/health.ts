/**
 * PowerMem backend health check: GET /api/v1/system/health (no auth required)
 * Uses Node.js http/https module for maximum compatibility across
 * VS Code, Cursor, CodeFuse, and other VS Code-based IDEs where
 * global fetch may not be available in the extension host.
 */

import * as http from 'http';
import * as https from 'https';

export async function checkHealth(baseUrl: string, timeoutMs = 5000): Promise<boolean> {
  const url = baseUrl.replace(/\/+$/, '') + '/api/v1/system/health';
  try {
    return await new Promise<boolean>((resolve) => {
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
          // Consume the response body to free the connection
          res.resume();
          resolve(res.statusCode !== undefined && res.statusCode >= 200 && res.statusCode < 300);
        }
      );
      req.on('error', (err) => {
        console.error('[PowerMem] health check error:', err.message);
        resolve(false);
      });
      req.on('timeout', () => {
        console.error('[PowerMem] health check timeout');
        req.destroy();
        resolve(false);
      });
      req.end();
    });
  } catch (err) {
    console.error('[PowerMem] health check unexpected error:', err);
    return false;
  }
}
