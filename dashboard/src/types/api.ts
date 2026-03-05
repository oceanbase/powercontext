/**
 * Type definitions for PowerMem API responses
 */

export interface DependencyStatus {
  name: string;
  status: "healthy" | "degraded" | "unavailable";
  latency_ms?: number;
  error_message?: string;
  last_checked: string;
}

export interface SystemStatus {
  status: "operational" | "degraded" | "down";
  version: string;
  storage_type?: string;
  llm_provider?: string;
  uptime_seconds: number;
  started_at: string;
  dependencies: Record<string, DependencyStatus>;
  timestamp: string;
}

export interface MemoryQualityMetrics {
  total_memories: number;
  low_quality_count: number;
  low_quality_ratio: number;
  quality_criteria: Record<string, number>;
}
