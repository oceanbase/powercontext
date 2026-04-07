/**
 * PowerMem backend detection (health + optional info)
 */

export async function detectBackend(url: string): Promise<boolean> {
  const base = url.replace(/\/+$/, '');
  try {
    const res = await fetch(`${base}/api/v1/system/health`, {
      method: 'GET',
      signal: AbortSignal.timeout(3000),
    });
    return res.ok;
  } catch {
    return false;
  }
}

export async function getBackendInfo(url: string): Promise<{ status?: string } | null> {
  try {
    const base = url.replace(/\/+$/, '');
    const res = await fetch(`${base}/api/v1/system/health`);
    if (!res.ok) return null;
    const json = (await res.json()) as { data?: { status?: string } };
    return json?.data ? { status: json.data.status } : null;
  } catch {
    return null;
  }
}
