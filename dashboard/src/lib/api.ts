/**
 * API client for the PowerMem Dashboard.
 */

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
  id: string | number;
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
  getStats: (filters?: { user_id?: string; agent_id?: string }) =>
    fetchWithAuth<MemoryStats>("/memories/stats", { params: filters }),

  getUsers: () => fetchWithAuth<string[]>("/memories/users"),

  getMemories: (params?: {
    user_id?: string;
    agent_id?: string;
    limit?: number;
    offset?: number;
    sort_by?: string;
    order?: string;
  }) => fetchWithAuth<MemoryList>("/memories", { params }),

  deleteMemory: (memoryId: string | number) =>
    fetchWithAuth<void>(`/memories/${memoryId}`, { method: "DELETE" }),

  bulkDeleteMemories: (memoryIds: (string | number)[], userId?: string) =>
    fetchWithAuth<{ deleted_count: number }>("/memories/batch", {
      method: "DELETE",
      body: { memory_ids: memoryIds, user_id: userId },
    }),
};
