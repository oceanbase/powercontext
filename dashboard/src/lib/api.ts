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

export interface SessionCapabilities {
  event_log?: boolean;
  memory_snapshot?: boolean;
  before_after_diff?: boolean;
  source_on_demand?: boolean;
}

export interface SessionSummary {
  run_id: string;
  user_id?: string;
  agent_id?: string;
  first_seen?: string;
  last_seen?: string;
  event_count: number;
  memory_count: number;
  latest_preview: string;
  precision: "memory_snapshot" | "event_log" | string;
}

export interface SessionList {
  sessions: SessionSummary[];
  total: number;
  limit: number;
  offset: number;
  precision: "memory_snapshot" | "event_log" | string;
  capabilities: SessionCapabilities;
}

export interface SessionStats {
  total_sessions: number;
  total_events: number;
  changed_memories: number;
  no_op_events: number;
  no_op_rate: number;
  event_types: Record<string, number>;
  precision: "memory_snapshot" | "event_log" | string;
  capabilities: SessionCapabilities;
}

export interface TimelineEvent {
  event_id: string;
  occurred_at?: string;
  run_id?: string;
  user_id?: string;
  agent_id?: string;
  memory_id?: string;
  event_type: string;
  pipeline_mode?: string;
  content_preview: string;
  metadata: Record<string, unknown>;
  source_preview?: string;
  source_content?: string;
  precision: "memory_snapshot" | "event_log" | string;
}

export interface TimelinePage {
  events: TimelineEvent[];
  total: number;
  limit: number;
  next_cursor?: string;
  order: "asc" | "desc" | string;
  precision: "memory_snapshot" | "event_log" | string;
  capabilities: SessionCapabilities;
}

export interface SearchResultItem {
  id: string;
  memory_id?: string;
  content: string;
  score?: number;
  user_id?: string;
  agent_id?: string;
  run_id?: string;
  metadata?: Record<string, any>;
  created_at?: string;
  updated_at?: string;
}

export interface SearchMemoriesResponse {
  results: SearchResultItem[];
  total: number;
  query: string;
}

export interface UserProfile {
  id: number;
  user_id: string;
  profile_content?: string;
  topics?: Record<string, any>;
  created_at?: string;
  updated_at?: string;
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
    run_id?: string;
    limit?: number;
    offset?: number;
    sort_by?: string;
    order?: string;
    time_range?: string;
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

  getSessions: (params?: {
    user_id?: string;
    agent_id?: string;
    run_id?: string;
    limit?: number;
    offset?: number;
    sort_by?: string;
    order?: string;
    time_range?: string;
  }) =>
    fetchWithAuth<
      SessionList &
        PaginationLike & {
          items?: SessionSummary[];
        }
    >("/memories/sessions", { params }).then((data) => {
      const sessions = data.sessions ?? data.items ?? [];
      return {
        sessions,
        total: normalizeTotal(data, sessions.length),
        limit: data.limit ?? params?.limit ?? sessions.length,
        offset: data.offset ?? params?.offset ?? 0,
        precision: data.precision ?? "memory_snapshot",
        capabilities: data.capabilities ?? { memory_snapshot: true },
      } satisfies SessionList;
    }),

  getSessionStats: (params?: {
    user_id?: string;
    agent_id?: string;
    run_id?: string;
    time_range?: string;
  }) =>
    fetchWithAuth<SessionStats>("/memories/session-stats", { params }).then((data) => ({
      total_sessions: data.total_sessions ?? 0,
      total_events: data.total_events ?? 0,
      changed_memories: data.changed_memories ?? 0,
      no_op_events: data.no_op_events ?? 0,
      no_op_rate: data.no_op_rate ?? 0,
      event_types: data.event_types ?? {},
      precision: data.precision ?? "memory_snapshot",
      capabilities: data.capabilities ?? { memory_snapshot: true },
    })),

  getTimeline: (params?: {
    user_id?: string;
    agent_id?: string;
    run_id?: string;
    event_type?: string;
    q?: string;
    cursor?: string;
    limit?: number;
    order?: string;
    time_range?: string;
    include_source?: boolean;
  }) =>
    fetchWithAuth<
      TimelinePage &
        PaginationLike & {
          items?: TimelineEvent[];
        }
    >("/memories/timeline", { params }).then((data) => {
      const events = data.events ?? data.items ?? [];
      return {
        events,
        total: normalizeTotal(data, events.length),
        limit: data.limit ?? params?.limit ?? events.length,
        next_cursor: data.next_cursor,
        order: data.order ?? params?.order ?? "desc",
        precision: data.precision ?? "memory_snapshot",
        capabilities: data.capabilities ?? { memory_snapshot: true },
      } satisfies TimelinePage;
    }),

  searchMemories: (params: {
    query: string;
    user_id?: string;
    agent_id?: string;
    run_id?: string;
    limit?: number;
    threshold?: number;
    time_range?: string;
    sort_by?: string;
    order?: string;
    filters?: Record<string, any>;
  }) =>
    fetchWithAuth<SearchMemoriesResponse>("/memories/search", {
      method: "POST",
      body: params,
    }).then((data) => ({
      results: data.results ?? [],
      total: data.total ?? (data.results?.length ?? 0),
      query: data.query ?? params.query,
    })),

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
