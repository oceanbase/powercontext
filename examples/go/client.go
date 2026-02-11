// Package main provides a simple HTTP client for the PowerMem API.
//
// This client demonstrates how to interact with PowerMem's HTTP API Server
// using Go's standard library. It supports all core memory operations
// including create, read, update, delete, and search.
package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strconv"
	"time"
)

// Client is a PowerMem API client.
type Client struct {
	// BaseURL is the base URL of the PowerMem API server.
	// Example: "http://localhost:8000"
	BaseURL string

	// APIKey is the API key for authentication.
	// Set via X-API-Key header.
	APIKey string

	// HTTPClient is the underlying HTTP client.
	// If nil, a default client with 30s timeout is used.
	HTTPClient *http.Client
}

// NewClient creates a new PowerMem API client.
func NewClient(baseURL, apiKey string) *Client {
	return &Client{
		BaseURL: baseURL,
		APIKey:  apiKey,
		HTTPClient: &http.Client{
			Timeout: 30 * time.Second,
		},
	}
}

// NewClientWithTimeout creates a new client with a custom timeout.
func NewClientWithTimeout(baseURL, apiKey string, timeout time.Duration) *Client {
	return &Client{
		BaseURL: baseURL,
		APIKey:  apiKey,
		HTTPClient: &http.Client{
			Timeout: timeout,
		},
	}
}

// =============================================================================
// Internal HTTP helpers
// =============================================================================

// doRequest performs an HTTP request and returns the response body.
func (c *Client) doRequest(method, path string, body interface{}) ([]byte, error) {
	var reqBody io.Reader
	if body != nil {
		jsonData, err := json.Marshal(body)
		if err != nil {
			return nil, fmt.Errorf("failed to marshal request body: %w", err)
		}
		reqBody = bytes.NewBuffer(jsonData)
	}

	req, err := http.NewRequest(method, c.BaseURL+path, reqBody)
	if err != nil {
		return nil, fmt.Errorf("failed to create request: %w", err)
	}

	// Set headers
	req.Header.Set("Content-Type", "application/json")
	if c.APIKey != "" {
		req.Header.Set("X-API-Key", c.APIKey)
	}

	// Execute request
	resp, err := c.HTTPClient.Do(req)
	if err != nil {
		return nil, fmt.Errorf("request failed: %w", err)
	}
	defer resp.Body.Close()

	// Read response body
	respBody, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("failed to read response body: %w", err)
	}

	// Check for HTTP errors
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		var apiResp APIResponse[any]
		if err := json.Unmarshal(respBody, &apiResp); err == nil && apiResp.Error != nil {
			return nil, fmt.Errorf("API error [%s]: %s", apiResp.Error.Code, apiResp.Error.Message)
		}
		return nil, fmt.Errorf("HTTP error %d: %s", resp.StatusCode, string(respBody))
	}

	return respBody, nil
}

// =============================================================================
// System Endpoints
// =============================================================================

// Health checks the health status of the API server.
// This endpoint is public and does not require authentication.
func (c *Client) Health() (*HealthResponse, error) {
	respBody, err := c.doRequest(http.MethodGet, "/api/v1/system/health", nil)
	if err != nil {
		return nil, err
	}

	var resp APIResponse[HealthResponse]
	if err := json.Unmarshal(respBody, &resp); err != nil {
		return nil, fmt.Errorf("failed to parse response: %w", err)
	}

	if !resp.Success {
		return nil, fmt.Errorf("health check failed: %s", resp.Message)
	}

	return &resp.Data, nil
}

// Status gets the system status and configuration information.
func (c *Client) Status() (*SystemStatusResponse, error) {
	respBody, err := c.doRequest(http.MethodGet, "/api/v1/system/status", nil)
	if err != nil {
		return nil, err
	}

	var resp APIResponse[SystemStatusResponse]
	if err := json.Unmarshal(respBody, &resp); err != nil {
		return nil, fmt.Errorf("failed to parse response: %w", err)
	}

	if !resp.Success {
		return nil, fmt.Errorf("status check failed: %s", resp.Message)
	}

	return &resp.Data, nil
}

// =============================================================================
// Memory CRUD Operations
// =============================================================================

// CreateMemory creates a new memory.
// When infer is true (default), PowerMem may extract multiple memories from the content.
func (c *Client) CreateMemory(req *CreateMemoryRequest) ([]CreatedMemory, error) {
	respBody, err := c.doRequest(http.MethodPost, "/api/v1/memories", req)
	if err != nil {
		return nil, err
	}

	var resp APIResponse[[]CreatedMemory]
	if err := json.Unmarshal(respBody, &resp); err != nil {
		return nil, fmt.Errorf("failed to parse response: %w", err)
	}

	if !resp.Success {
		return nil, fmt.Errorf("create memory failed: %s", resp.Message)
	}

	return resp.Data, nil
}

// GetMemory retrieves a single memory by ID.
func (c *Client) GetMemory(memoryID MemoryID, userID, agentID string) (*Memory, error) {
	// Build query parameters
	params := url.Values{}
	if userID != "" {
		params.Set("user_id", userID)
	}
	if agentID != "" {
		params.Set("agent_id", agentID)
	}

	path := fmt.Sprintf("/api/v1/memories/%s", memoryID.String())
	if len(params) > 0 {
		path += "?" + params.Encode()
	}

	respBody, err := c.doRequest(http.MethodGet, path, nil)
	if err != nil {
		return nil, err
	}

	var resp APIResponse[Memory]
	if err := json.Unmarshal(respBody, &resp); err != nil {
		return nil, fmt.Errorf("failed to parse response: %w", err)
	}

	if !resp.Success {
		return nil, fmt.Errorf("get memory failed: %s", resp.Message)
	}

	return &resp.Data, nil
}

// ListMemories retrieves a list of memories with optional filtering and pagination.
func (c *Client) ListMemories(params ListMemoriesParams) (*MemoryList, error) {
	// Build query parameters
	queryParams := url.Values{}
	if params.UserID != "" {
		queryParams.Set("user_id", params.UserID)
	}
	if params.AgentID != "" {
		queryParams.Set("agent_id", params.AgentID)
	}
	if params.Limit > 0 {
		queryParams.Set("limit", strconv.Itoa(params.Limit))
	}
	if params.Offset > 0 {
		queryParams.Set("offset", strconv.Itoa(params.Offset))
	}
	if params.SortBy != "" {
		queryParams.Set("sort_by", params.SortBy)
	}
	if params.Order != "" {
		queryParams.Set("order", params.Order)
	}

	path := "/api/v1/memories"
	if len(queryParams) > 0 {
		path += "?" + queryParams.Encode()
	}

	respBody, err := c.doRequest(http.MethodGet, path, nil)
	if err != nil {
		return nil, err
	}

	var resp APIResponse[MemoryList]
	if err := json.Unmarshal(respBody, &resp); err != nil {
		return nil, fmt.Errorf("failed to parse response: %w", err)
	}

	if !resp.Success {
		return nil, fmt.Errorf("list memories failed: %s", resp.Message)
	}

	return &resp.Data, nil
}

// UpdateMemory updates an existing memory.
func (c *Client) UpdateMemory(memoryID MemoryID, req *UpdateMemoryRequest) (*Memory, error) {
	path := fmt.Sprintf("/api/v1/memories/%s", memoryID.String())

	respBody, err := c.doRequest(http.MethodPut, path, req)
	if err != nil {
		return nil, err
	}

	var resp APIResponse[Memory]
	if err := json.Unmarshal(respBody, &resp); err != nil {
		return nil, fmt.Errorf("failed to parse response: %w", err)
	}

	if !resp.Success {
		return nil, fmt.Errorf("update memory failed: %s", resp.Message)
	}

	return &resp.Data, nil
}

// DeleteMemory deletes a single memory by ID.
func (c *Client) DeleteMemory(memoryID MemoryID, userID, agentID string) error {
	// Build query parameters
	params := url.Values{}
	if userID != "" {
		params.Set("user_id", userID)
	}
	if agentID != "" {
		params.Set("agent_id", agentID)
	}

	path := fmt.Sprintf("/api/v1/memories/%s", memoryID.String())
	if len(params) > 0 {
		path += "?" + params.Encode()
	}

	respBody, err := c.doRequest(http.MethodDelete, path, nil)
	if err != nil {
		return err
	}

	var resp APIResponse[DeleteMemoryResponse]
	if err := json.Unmarshal(respBody, &resp); err != nil {
		return fmt.Errorf("failed to parse response: %w", err)
	}

	if !resp.Success {
		return fmt.Errorf("delete memory failed: %s", resp.Message)
	}

	return nil
}

// =============================================================================
// Search Operations
// =============================================================================

// SearchMemories performs a semantic search for memories.
func (c *Client) SearchMemories(req *SearchMemoryRequest) (*SearchResults, error) {
	respBody, err := c.doRequest(http.MethodPost, "/api/v1/memories/search", req)
	if err != nil {
		return nil, err
	}

	var resp APIResponse[SearchResults]
	if err := json.Unmarshal(respBody, &resp); err != nil {
		return nil, fmt.Errorf("failed to parse response: %w", err)
	}

	if !resp.Success {
		return nil, fmt.Errorf("search memories failed: %s", resp.Message)
	}

	return &resp.Data, nil
}

// =============================================================================
// User Memory Operations
// =============================================================================

// GetUserMemories retrieves all memories for a specific user.
func (c *Client) GetUserMemories(userID string, limit, offset int) (*MemoryList, error) {
	params := url.Values{}
	if limit > 0 {
		params.Set("limit", strconv.Itoa(limit))
	}
	if offset > 0 {
		params.Set("offset", strconv.Itoa(offset))
	}

	path := fmt.Sprintf("/api/v1/users/%s/memories", userID)
	if len(params) > 0 {
		path += "?" + params.Encode()
	}

	respBody, err := c.doRequest(http.MethodGet, path, nil)
	if err != nil {
		return nil, err
	}

	var resp APIResponse[MemoryList]
	if err := json.Unmarshal(respBody, &resp); err != nil {
		return nil, fmt.Errorf("failed to parse response: %w", err)
	}

	if !resp.Success {
		return nil, fmt.Errorf("get user memories failed: %s", resp.Message)
	}

	return &resp.Data, nil
}
