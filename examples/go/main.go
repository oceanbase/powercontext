// Package main demonstrates how to use the PowerMem HTTP API with Go.
//
// This example shows all core memory operations:
// - Health check
// - Create memory
// - List memories
// - Search memories
// - Update memory
// - Delete memory
//
// Usage:
//
//	go run .
//
// Environment variables:
//
//	POWERMEM_BASE_URL - Base URL of the PowerMem API server (default: http://localhost:8000)
//	POWERMEM_API_KEY  - API key for authentication (optional if auth is disabled)
package main

import (
	"fmt"
	"os"
	"strings"
)

func main() {
	fmt.Println(strings.Repeat("=", 60))
	fmt.Println("PowerMem Go Client Example")
	fmt.Println(strings.Repeat("=", 60))

	// Initialize client from environment or defaults
	client := initClient()

	// Run all examples
	if err := runExamples(client); err != nil {
		fmt.Printf("\n❌ Error: %v\n", err)
		os.Exit(1)
	}

	fmt.Println("\n" + strings.Repeat("=", 60))
	fmt.Println("✅ All examples completed successfully!")
	fmt.Println(strings.Repeat("=", 60))
}

// initClient creates a PowerMem client from environment variables.
func initClient() *Client {
	baseURL := os.Getenv("POWERMEM_BASE_URL")
	if baseURL == "" {
		baseURL = "http://localhost:8000"
	}

	apiKey := os.Getenv("POWERMEM_API_KEY")

	fmt.Printf("\nConfiguration:\n")
	fmt.Printf("  Base URL: %s\n", baseURL)
	if apiKey != "" {
		fmt.Printf("  API Key:  %s...%s\n", apiKey[:3], apiKey[len(apiKey)-3:])
	} else {
		fmt.Printf("  API Key:  (not set)\n")
	}

	return NewClient(baseURL, apiKey)
}

// runExamples executes all example operations.
func runExamples(client *Client) error {
	// 1. Health Check
	if err := exampleHealthCheck(client); err != nil {
		return fmt.Errorf("health check failed: %w", err)
	}

	// 2. Create Memory
	memories, err := exampleCreateMemory(client)
	if err != nil {
		fmt.Printf("⚠️  Create memory skipped: %v\n", err)
		fmt.Println("   (Requires valid EMBEDDING_API_KEY in server .env)")
		memories = nil
	}

	// 3. List Memories
	if err := exampleListMemories(client); err != nil {
		fmt.Printf("⚠️  List memories skipped: %v\n", err)
	}

	// 4. Search Memories
	if err := exampleSearchMemories(client); err != nil {
		fmt.Printf("⚠️  Search memories skipped: %v\n", err)
		fmt.Println("   (Requires valid EMBEDDING_API_KEY in server .env)")
	}

	// 5. Update Memory (if we have created memories)
	if len(memories) > 0 {
		if err := exampleUpdateMemory(client, memories[0].MemoryID); err != nil {
			fmt.Printf("⚠️  Update memory skipped: %v\n", err)
		}
	} else {
		fmt.Println("\n" + strings.Repeat("-", 40))
		fmt.Println("5. Update Memory")
		fmt.Println(strings.Repeat("-", 40))
		fmt.Println("⚠️  Skipped (no memories created)")
	}

	// 6. Delete Memory (if we have created memories)
	if len(memories) > 0 {
		if err := exampleDeleteMemory(client, memories[0].MemoryID); err != nil {
			fmt.Printf("⚠️  Delete memory skipped: %v\n", err)
		}
	} else {
		fmt.Println("\n" + strings.Repeat("-", 40))
		fmt.Println("6. Delete Memory")
		fmt.Println(strings.Repeat("-", 40))
		fmt.Println("⚠️  Skipped (no memories created)")
	}

	return nil
}

// =============================================================================
// Example Functions
// =============================================================================

// exampleHealthCheck demonstrates the health check endpoint.
func exampleHealthCheck(client *Client) error {
	fmt.Println("\n" + strings.Repeat("-", 40))
	fmt.Println("1. Health Check")
	fmt.Println(strings.Repeat("-", 40))

	health, err := client.Health()
	if err != nil {
		return err
	}

	fmt.Printf("✓ Status: %s\n", health.Status)
	fmt.Printf("  Timestamp: %s\n", health.Timestamp.Format("2006-01-02 15:04:05"))

	return nil
}

// exampleCreateMemory demonstrates creating memories.
func exampleCreateMemory(client *Client) ([]CreatedMemory, error) {
	fmt.Println("\n" + strings.Repeat("-", 40))
	fmt.Println("2. Create Memory")
	fmt.Println(strings.Repeat("-", 40))

	// Create a memory with intelligent extraction enabled
	// PowerMem will automatically extract multiple facts from the content
	infer := true
	req := &CreateMemoryRequest{
		Content: "User likes coffee and goes to Starbucks every morning. They prefer latte.",
		UserID:  "go-example-user",
		AgentID: "go-example-agent",
		Metadata: map[string]interface{}{
			"source":     "go-client-example",
			"importance": "high",
		},
		Infer: &infer,
	}

	memories, err := client.CreateMemory(req)
	if err != nil {
		return nil, err
	}

	fmt.Printf("✓ Created %d memory(ies):\n", len(memories))
	for i, mem := range memories {
		fmt.Printf("  [%d] ID: %s\n", i+1, mem.MemoryID.String())
		fmt.Printf("      Content: %s\n", mem.Content)
	}

	return memories, nil
}

// exampleListMemories demonstrates listing memories with pagination.
func exampleListMemories(client *Client) error {
	fmt.Println("\n" + strings.Repeat("-", 40))
	fmt.Println("3. List Memories")
	fmt.Println(strings.Repeat("-", 40))

	params := ListMemoriesParams{
		UserID: "go-example-user",
		Limit:  10,
		Offset: 0,
		SortBy: "created_at",
		Order:  "desc",
	}

	list, err := client.ListMemories(params)
	if err != nil {
		return err
	}

	fmt.Printf("✓ Found %d memories (showing %d):\n", list.Total, len(list.Memories))
	for i, mem := range list.Memories {
		content := mem.Content
		if len(content) > 50 {
			content = content[:47] + "..."
		}
		fmt.Printf("  [%d] ID: %s\n", i+1, mem.MemoryID.String())
		fmt.Printf("      Content: %s\n", content)
	}

	return nil
}

// exampleSearchMemories demonstrates semantic search.
func exampleSearchMemories(client *Client) error {
	fmt.Println("\n" + strings.Repeat("-", 40))
	fmt.Println("4. Search Memories")
	fmt.Println(strings.Repeat("-", 40))

	req := &SearchMemoryRequest{
		Query:   "What beverages does the user like?",
		UserID:  "go-example-user",
		AgentID: "go-example-agent",
		Limit:   5,
	}

	results, err := client.SearchMemories(req)
	if err != nil {
		return err
	}

	fmt.Printf("✓ Query: %q\n", results.Query)
	fmt.Printf("  Found %d results:\n", results.Total)
	for i, result := range results.Results {
		content := result.Content
		if len(content) > 50 {
			content = content[:47] + "..."
		}
		fmt.Printf("  [%d] Score: %.4f\n", i+1, result.Score)
		fmt.Printf("      ID: %s\n", result.MemoryID.String())
		fmt.Printf("      Content: %s\n", content)
	}

	return nil
}

// exampleUpdateMemory demonstrates updating a memory.
func exampleUpdateMemory(client *Client, memoryID MemoryID) error {
	fmt.Println("\n" + strings.Repeat("-", 40))
	fmt.Println("5. Update Memory")
	fmt.Println(strings.Repeat("-", 40))

	req := &UpdateMemoryRequest{
		Content: "User loves espresso and visits Starbucks daily",
		UserID:  "go-example-user",
		AgentID: "go-example-agent",
		Metadata: map[string]interface{}{
			"source":     "go-client-example",
			"importance": "very-high",
			"updated":    true,
		},
	}

	memory, err := client.UpdateMemory(memoryID, req)
	if err != nil {
		return err
	}

	fmt.Printf("✓ Updated memory ID: %s\n", memory.MemoryID.String())
	fmt.Printf("  New content: %s\n", memory.Content)
	if memory.UpdatedAt != nil {
		fmt.Printf("  Updated at: %s\n", memory.UpdatedAt.Format("2006-01-02 15:04:05"))
	}

	return nil
}

// exampleDeleteMemory demonstrates deleting a memory.
func exampleDeleteMemory(client *Client, memoryID MemoryID) error {
	fmt.Println("\n" + strings.Repeat("-", 40))
	fmt.Println("6. Delete Memory")
	fmt.Println(strings.Repeat("-", 40))

	err := client.DeleteMemory(memoryID, "go-example-user", "go-example-agent")
	if err != nil {
		return err
	}

	fmt.Printf("✓ Deleted memory ID: %s\n", memoryID.String())

	return nil
}
