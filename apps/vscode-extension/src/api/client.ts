/**
 * PowerMem HTTP API client for extension commands (search, add memory).
 * Base URL e.g. http://localhost:8000; endpoints: /api/v1/memories/search, /api/v1/memories
 */

import type {
  ApiResponse,
  SearchRequest,
  SearchResponseData,
  MemoryCreateRequest,
  MemoryCreateResponseDataItem,
} from './types';

function ensureNoTrailingSlash(baseUrl: string): string {
  return baseUrl.replace(/\/+$/, '');
}

function getHeaders(apiKey?: string): Record<string, string> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  if (apiKey) headers['X-API-Key'] = apiKey;
  return headers;
}

export async function searchMemories(
  baseUrl: string,
  request: SearchRequest,
  apiKey?: string
): Promise<SearchResponseData> {
  const url = `${ensureNoTrailingSlash(baseUrl)}/api/v1/memories/search`;
  const res = await fetch(url, {
    method: 'POST',
    headers: getHeaders(apiKey),
    body: JSON.stringify({
      query: request.query,
      user_id: request.user_id ?? undefined,
      agent_id: request.agent_id ?? undefined,
      run_id: request.run_id ?? undefined,
      limit: request.limit ?? 10,
    }),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`PowerMem search failed: ${res.status} ${text}`);
  }
  const json = (await res.json()) as ApiResponse<SearchResponseData>;
  if (!json.success || !json.data) {
    throw new Error(json.message || 'Search failed');
  }
  return json.data;
}

export async function addMemory(
  baseUrl: string,
  request: MemoryCreateRequest,
  apiKey?: string
): Promise<MemoryCreateResponseDataItem[]> {
  const url = `${ensureNoTrailingSlash(baseUrl)}/api/v1/memories`;
  const res = await fetch(url, {
    method: 'POST',
    headers: getHeaders(apiKey),
    body: JSON.stringify({
      content: request.content,
      user_id: request.user_id ?? undefined,
      agent_id: request.agent_id ?? undefined,
      run_id: request.run_id ?? undefined,
      metadata: request.metadata ?? undefined,
      infer: request.infer ?? true,
    }),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`PowerMem add memory failed: ${res.status} ${text}`);
  }
  const json = (await res.json()) as ApiResponse<MemoryCreateResponseDataItem[]>;
  if (!json.success) {
    throw new Error(json.message || 'Add memory failed');
  }
  const data = json.data;
  return Array.isArray(data) ? data : [];
}
