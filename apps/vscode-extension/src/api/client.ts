/**
 * PowerMem HTTP API client for extension commands (search, add memory).
 * Base URL e.g. http://localhost:8848; endpoints: /api/v1/memories/search, /api/v1/memories
 * Uses Node.js http/https module for maximum compatibility across
 * VS Code, Cursor, CodeFuse, and other VS Code-based IDEs where
 * global fetch may not be available in the extension host.
 */

import * as http from 'http';
import * as https from 'https';

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

/**
 * Robust HTTP request helper using Node.js http/https module.
 * Returns { status, body } or throws on network error.
 */
async function httpRequest(
  method: string,
  url: string,
  headers: Record<string, string>,
  body?: string,
  timeoutMs = 10000
): Promise<{ status: number; body: string }> {
  return new Promise((resolve, reject) => {
    const parsed = new URL(url);
    const mod = parsed.protocol === 'https:' ? https : http;
    const req = mod.request(
      {
        hostname: parsed.hostname,
        port: parsed.port || (parsed.protocol === 'https:' ? 443 : 80),
        path: parsed.pathname + parsed.search,
        method,
        headers,
        timeout: timeoutMs,
      },
      (res) => {
        const chunks: Buffer[] = [];
        res.on('data', (chunk: Buffer) => chunks.push(chunk));
        res.on('end', () => {
          const responseBody = Buffer.concat(chunks).toString('utf-8');
          resolve({ status: res.statusCode ?? 0, body: responseBody });
        });
      }
    );
    req.on('error', (err) => reject(err));
    req.on('timeout', () => {
      req.destroy();
      reject(new Error(`Request timed out after ${timeoutMs}ms`));
    });
    if (body) {
      req.write(body);
    }
    req.end();
  });
}

export async function searchMemories(
  baseUrl: string,
  request: SearchRequest,
  apiKey?: string
): Promise<SearchResponseData> {
  const url = `${ensureNoTrailingSlash(baseUrl)}/api/v1/memories/search`;
  const res = await httpRequest(
    'POST',
    url,
    getHeaders(apiKey),
    JSON.stringify({
      query: request.query,
      user_id: request.user_id ?? undefined,
      agent_id: request.agent_id ?? undefined,
      run_id: request.run_id ?? undefined,
      limit: request.limit ?? 10,
    })
  );
  if (res.status < 200 || res.status >= 300) {
    throw new Error(`PowerMem search failed: ${res.status} ${res.body}`);
  }
  const json = JSON.parse(res.body) as ApiResponse<SearchResponseData>;
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
  const res = await httpRequest(
    'POST',
    url,
    getHeaders(apiKey),
    JSON.stringify({
      content: request.content,
      user_id: request.user_id ?? undefined,
      agent_id: request.agent_id ?? undefined,
      run_id: request.run_id ?? undefined,
      metadata: request.metadata ?? undefined,
      infer: request.infer ?? true,
    })
  );
  if (res.status < 200 || res.status >= 300) {
    throw new Error(`PowerMem add memory failed: ${res.status} ${res.body}`);
  }
  const json = JSON.parse(res.body) as ApiResponse<MemoryCreateResponseDataItem[]>;
  if (!json.success) {
    throw new Error(json.message || 'Add memory failed');
  }
  const data = json.data;
  return Array.isArray(data) ? data : [];
}
