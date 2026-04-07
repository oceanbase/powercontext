/**
 * Types for PowerMem HTTP API (align with docs/api/0005-api_server.md)
 */

export interface SearchResultItem {
  memory_id: string;
  content: string;
  score?: number;
  metadata?: Record<string, unknown>;
}

export interface SearchResponseData {
  results: SearchResultItem[];
  total: number;
  query: string;
}

export interface ApiResponse<T = unknown> {
  success: boolean;
  data?: T;
  message?: string;
  timestamp?: string;
}

export interface MemoryCreateResponseDataItem {
  memory_id: number;
  content: string;
  user_id?: string;
  agent_id?: string;
  run_id?: string;
  metadata?: Record<string, unknown>;
}

export interface MemoryCreateResponseData {
  data?: MemoryCreateResponseDataItem[];
}

export interface SearchRequest {
  query: string;
  user_id?: string;
  agent_id?: string;
  run_id?: string;
  limit?: number;
}

export interface MemoryCreateRequest {
  content: string;
  user_id?: string;
  agent_id?: string;
  run_id?: string;
  metadata?: Record<string, unknown>;
  infer?: boolean;
}
