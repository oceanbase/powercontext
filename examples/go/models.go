// Package main provides data models for PowerMem API requests and responses.
//
// Note: Memory IDs are 64-bit integers that may exceed JavaScript's safe integer range.
// To avoid precision loss, memory_id is handled as a string in JSON serialization.
package main

import (
	"encoding/json"
	"strconv"
	"time"
)

// MemoryID is a custom type for handling 64-bit memory IDs.
// It marshals/unmarshals as a JSON number but is stored as int64 in Go.
type MemoryID int64

// MarshalJSON implements json.Marshaler for MemoryID.
func (m MemoryID) MarshalJSON() ([]byte, error) {
	return json.Marshal(int64(m))
}

// UnmarshalJSON implements json.Unmarshaler for MemoryID.
// It handles both number and string representations for compatibility.
func (m *MemoryID) UnmarshalJSON(data []byte) error {
	// Try to unmarshal as number first
	var n int64
	if err := json.Unmarshal(data, &n); err == nil {
		*m = MemoryID(n)
		return nil
	}

	// Try to unmarshal as string (for large integers)
	var s string
	if err := json.Unmarshal(data, &s); err == nil {
		n, err := strconv.ParseInt(s, 10, 64)
		if err != nil {
			return err
		}
		*m = MemoryID(n)
		return nil
	}

	return nil
}

// String returns the string representation of the MemoryID.
func (m MemoryID) String() string {
	return strconv.FormatInt(int64(m), 10)
}

// Int64 returns the int64 value of the MemoryID.
func (m MemoryID) Int64() int64 {
	return int64(m)
}

// =============================================================================
// API Response Wrapper
// =============================================================================

// APIResponse is the standard response wrapper for all PowerMem API responses.
type APIResponse[T any] struct {
	Success   bool      `json:"success"`
	Data      T         `json:"data,omitempty"`
	Message   string    `json:"message"`
	Timestamp time.Time `json:"timestamp"`
	Error     *APIError `json:"error,omitempty"`
}

// APIError represents an error response from the API.
type APIError struct {
	Code    string `json:"code"`
	Message string `json:"message"`
}

// =============================================================================
// Memory Models
// =============================================================================

// Memory represents a memory record in PowerMem.
type Memory struct {
	MemoryID  MemoryID               `json:"memory_id"`
	Content   string                 `json:"content"`
	UserID    string                 `json:"user_id,omitempty"`
	AgentID   string                 `json:"agent_id,omitempty"`
	RunID     string                 `json:"run_id,omitempty"`
	Metadata  map[string]interface{} `json:"metadata,omitempty"`
	CreatedAt *time.Time             `json:"created_at,omitempty"`
	UpdatedAt *time.Time             `json:"updated_at,omitempty"`
}

// MemoryList represents a paginated list of memories.
type MemoryList struct {
	Memories []Memory `json:"memories"`
	Total    int      `json:"total"`
	Limit    int      `json:"limit"`
	Offset   int      `json:"offset"`
}

// =============================================================================
// Create Memory
// =============================================================================

// CreateMemoryRequest represents the request body for creating a memory.
type CreateMemoryRequest struct {
	Content    string                 `json:"content"`
	UserID     string                 `json:"user_id,omitempty"`
	AgentID    string                 `json:"agent_id,omitempty"`
	RunID      string                 `json:"run_id,omitempty"`
	Metadata   map[string]interface{} `json:"metadata,omitempty"`
	Filters    map[string]interface{} `json:"filters,omitempty"`
	Scope      string                 `json:"scope,omitempty"`
	MemoryType string                 `json:"memory_type,omitempty"`
	Infer      *bool                  `json:"infer,omitempty"`
}

// CreatedMemory represents a simplified memory returned after creation.
type CreatedMemory struct {
	MemoryID MemoryID               `json:"memory_id"`
	Content  string                 `json:"content"`
	UserID   string                 `json:"user_id,omitempty"`
	AgentID  string                 `json:"agent_id,omitempty"`
	RunID    string                 `json:"run_id,omitempty"`
	Metadata map[string]interface{} `json:"metadata,omitempty"`
}

// =============================================================================
// Update Memory
// =============================================================================

// UpdateMemoryRequest represents the request body for updating a memory.
type UpdateMemoryRequest struct {
	Content  string                 `json:"content,omitempty"`
	UserID   string                 `json:"user_id,omitempty"`
	AgentID  string                 `json:"agent_id,omitempty"`
	Metadata map[string]interface{} `json:"metadata,omitempty"`
}

// =============================================================================
// Search Memory
// =============================================================================

// SearchMemoryRequest represents the request body for searching memories.
type SearchMemoryRequest struct {
	Query   string                 `json:"query"`
	UserID  string                 `json:"user_id,omitempty"`
	AgentID string                 `json:"agent_id,omitempty"`
	RunID   string                 `json:"run_id,omitempty"`
	Filters map[string]interface{} `json:"filters,omitempty"`
	Limit   int                    `json:"limit,omitempty"`
}

// SearchResult represents a single search result.
type SearchResult struct {
	MemoryID MemoryID               `json:"memory_id"`
	Content  string                 `json:"content"`
	Score    float64                `json:"score"`
	Metadata map[string]interface{} `json:"metadata,omitempty"`
}

// SearchResults represents the search response data.
type SearchResults struct {
	Results []SearchResult `json:"results"`
	Total   int            `json:"total"`
	Query   string         `json:"query"`
}

// =============================================================================
// Delete Memory
// =============================================================================

// DeleteMemoryResponse represents the response data for a delete operation.
type DeleteMemoryResponse struct {
	MemoryID MemoryID `json:"memory_id"`
}

// =============================================================================
// System Endpoints
// =============================================================================

// HealthResponse represents the health check response data.
type HealthResponse struct {
	Status    string    `json:"status"`
	Timestamp time.Time `json:"timestamp"`
}

// SystemStatusResponse represents the system status response data.
type SystemStatusResponse struct {
	Status      string    `json:"status"`
	Version     string    `json:"version"`
	StorageType string    `json:"storage_type"`
	LLMProvider string    `json:"llm_provider"`
	Timestamp   time.Time `json:"timestamp"`
}

// =============================================================================
// List Parameters
// =============================================================================

// ListMemoriesParams contains parameters for listing memories.
type ListMemoriesParams struct {
	UserID  string
	AgentID string
	Limit   int
	Offset  int
	SortBy  string // created_at, updated_at, id
	Order   string // asc, desc
}

// DefaultListParams returns default list parameters.
func DefaultListParams() ListMemoriesParams {
	return ListMemoriesParams{
		Limit:  100,
		Offset: 0,
		Order:  "desc",
	}
}
