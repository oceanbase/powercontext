/**
 * API client for the PowerMem Dashboard.
 */

import type { SystemStatus, MemoryQualityMetrics } from "../types/api";

const BASE_URL = "/api/v1";

export interface MemoryStats {
  total_memories: number;
  by_type: Record<string, number>;
  avg_importance: number;
  top_accessed: Array<{
    id: number | string;
    content: string;
    access_count: number;
  }>;
  growth_trend: Record<string, number>;
  age_distribution: {
    "< 1 day": number;
    "1-7 days": number;
    "7-30 days": number;
    "> 30 days": number;
  };
}

export interface Memory {
  id: string;
  memory_id?: string;
  content: string;
  user_id?: string;
  agent_id?: string;
  run_id?: string;
  category?: string;
  metadata?: Record<string, any>;
  created_at: string;
  updated_at: string;
}

export interface MemoryList {
  memories: Memory[];
  total: number;
  limit: number;
  offset: number;
}

export interface UserProfile {
  id: number;
  user_id: string;
  profile_content?: string;
  topics?: Record<string, any>;
  created_at: string;
  updated_at: string;
}

export interface UserProfileListResponse {
  profiles: UserProfile[];
  total: number;
  limit: number;
  offset: number;
}

type PaginationLike = {
  total?: number;
  total_count?: number;
  total_memories?: number;
  count?: number;
  limit?: number;
  offset?: number;
};

const normalizeTotal = <T extends PaginationLike>(data: T, fallback: number): number => {
  return (
    data.total ??
    data.total_count ??
    data.total_memories ??
    data.count ??
    fallback
  );
};

export interface ApiResponse<T> {
  success: boolean;
  data: T;
  message?: string;
}

/**
 * Get the API key from local storage.
 */
export const getApiKey = (): string => {
  return localStorage.getItem("powermem_api_key") || "";
};

/**
 * Set the API key in local storage.
 */
export const setApiKey = (key: string): void => {
  localStorage.setItem("powermem_api_key", key);
};

/**
 * Generic fetch wrapper with API key.
 */
async function fetchWithAuth<T>(
  endpoint: string,
  options: {
    method?: string;
    params?: Record<string, any>;
    body?: any;
  } = {},
): Promise<T> {
  const { method = "GET", params, body } = options;
  const url = new URL(`${window.location.origin}${BASE_URL}${endpoint}`);
  if (params) {
    Object.entries(params).forEach(([key, value]) => {
      if (value !== undefined && value !== null)
        url.searchParams.append(key, String(value));
    });
  }

  const response = await fetch(url.toString(), {
    method,
    headers: {
      "X-API-Key": getApiKey(),
      "Content-Type": "application/json",
    },
    body: body ? JSON.stringify(body) : undefined,
  });

  if (!response.ok) {
    throw new Error(`API error: ${response.status} ${response.statusText}`);
  }

  const result: ApiResponse<T> = await response.json();
  if (!result.success) {
    throw new Error(result.message || "Unknown API error");
  }

  return result.data;
}

/**
 * API methods
 */
export const api = {
  getStats: (filters?: { user_id?: string; agent_id?: string; time_range?: string }) =>
    fetchWithAuth<MemoryStats>("/memories/stats", { params: filters }),

  getMemories: (params?: {
    user_id?: string;
    agent_id?: string;
    limit?: number;
    offset?: number;
    sort_by?: string;
    order?: string;
  }) =>
    fetchWithAuth<
      MemoryList &
        PaginationLike & {
          items?: Memory[];
        }
    >("/memories", { params }).then((data) => {
      const memories = data.memories ?? data.items ?? [];
      return {
        memories,
        total: normalizeTotal(data, memories.length),
        limit: data.limit ?? params?.limit ?? memories.length,
        offset: data.offset ?? params?.offset ?? 0,
      } satisfies MemoryList;
    }),

  deleteMemory: (memoryId: string) =>
    fetchWithAuth<void>(`/memories/${memoryId}`, { method: "DELETE" }),

  bulkDeleteMemories: (memoryIds: string[], userId?: string) =>
    fetchWithAuth<{ deleted_count: number }>("/memories/batch", {
      method: "DELETE",
      body: { memory_ids: memoryIds, user_id: userId },
    }),

  getSystemStatus: () =>
    fetchWithAuth<SystemStatus>("/system/status"),

  getMemoryQuality: (params?: { user_id?: string; agent_id?: string; time_range?: string }) =>
    fetchWithAuth<MemoryQualityMetrics>("/memories/quality", { params }),

  getAllUserProfiles: (user_id?: string, limit?: number, offset?: number, fuzzy?: boolean) => {
    const params: Record<string, any> = {};
    if (user_id) params.user_id = user_id;
    if (limit !== undefined) params.limit = limit;
    if (offset !== undefined) params.offset = offset;
    if (fuzzy !== undefined) params.fuzzy = fuzzy;
    return fetchWithAuth<
      UserProfileListResponse &
        PaginationLike & {
          users?: UserProfile[];
        }
    >("/users/profiles", { params }).then((data) => {
      const profiles = data.profiles ?? data.users ?? [];
      return {
        profiles,
        total: normalizeTotal(data, profiles.length),
        limit: data.limit ?? limit ?? profiles.length,
        offset: data.offset ?? offset ?? 0,
      } satisfies UserProfileListResponse;
    });
  },
};
