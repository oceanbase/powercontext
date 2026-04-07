/**
 * PowerMem backend health check: GET /api/v1/system/health (no auth required)
 */

export async function checkHealth(baseUrl: string, timeoutMs = 5000): Promise<boolean> {
  const url = baseUrl.replace(/\/+$/, '') + '/api/v1/system/health';
  try {
    const controller = new AbortController();
    const id = setTimeout(() => controller.abort(), timeoutMs);
    const res = await fetch(url, { method: 'GET', signal: controller.signal });
    clearTimeout(id);
    return res.ok;
  } catch {
    return false;
  }
}
