package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

const unitSecret = "Bearer unitsecret123"

func setHookScrubForTest(t *testing.T, enabled bool) {
	t.Helper()
	if enabled {
		t.Setenv("POWERMEM_HOOK_SCRUB", "1")
	} else {
		t.Setenv("POWERMEM_HOOK_SCRUB", "0")
	}
}

func requireNoUnitSecret(t *testing.T, v any) {
	t.Helper()
	b, err := json.Marshal(v)
	if err != nil {
		t.Fatalf("marshal value: %v", err)
	}
	if strings.Contains(string(b), unitSecret) {
		t.Fatalf("value leaked unit secret: %s", b)
	}
}

func TestHookScrubConfigDefaultsOn(t *testing.T) {
	t.Setenv("POWERMEM_HOOK_SCRUB", "")
	if !loadHookPrivacyConfig().Enabled {
		t.Fatal("empty scrub setting should default to enabled")
	}
	t.Setenv("POWERMEM_HOOK_SCRUB", "unexpected")
	if !loadHookPrivacyConfig().Enabled {
		t.Fatal("unknown scrub setting should default to enabled")
	}
	for _, raw := range []string{"0", "false", "no", "off", " OFF "} {
		t.Run("disable_"+strings.TrimSpace(raw), func(t *testing.T) {
			t.Setenv("POWERMEM_HOOK_SCRUB", raw)
			if loadHookPrivacyConfig().Enabled {
				t.Fatalf("scrub setting %q should disable scrubbing", raw)
			}
		})
	}
	for _, raw := range []string{"1", "true", "yes", "on", " YES "} {
		t.Run("enable_"+strings.TrimSpace(raw), func(t *testing.T) {
			t.Setenv("POWERMEM_HOOK_SCRUB", raw)
			if !loadHookPrivacyConfig().Enabled {
				t.Fatalf("scrub setting %q should enable scrubbing", raw)
			}
		})
	}
}

func TestToolEventAllowedIncludeExcludePrecedence(t *testing.T) {
	t.Setenv("POWERMEM_TOOL_SUCCESS_INCLUDE", "")
	t.Setenv("POWERMEM_TOOL_SUCCESS_EXCLUDE", "")
	if !toolEventAllowed("Bash") {
		t.Fatal("Bash should be included by default")
	}
	if toolEventAllowed("NotebookEdit") {
		t.Fatal("unknown tools should not be included by default")
	}

	t.Setenv("POWERMEM_TOOL_SUCCESS_INCLUDE", "*")
	t.Setenv("POWERMEM_TOOL_SUCCESS_EXCLUDE", "Bash")
	if toolEventAllowed("Bash") {
		t.Fatal("exclude should win over wildcard include")
	}
	if !toolEventAllowed("NotebookEdit") {
		t.Fatal("wildcard include should allow otherwise unknown tools")
	}

	t.Setenv("POWERMEM_TOOL_SUCCESS_EXCLUDE", "*")
	if toolEventAllowed("Write") {
		t.Fatal("wildcard exclude should disable all tool capture")
	}
}

func TestPrepareToolEventHandoffScrubsAndDropsRawPayload(t *testing.T) {
	setHookScrubForTest(t, true)
	t.Setenv("POWERMEM_TOOL_SUCCESS_INCLUDE", "")
	t.Setenv("POWERMEM_TOOL_SUCCESS_EXCLUDE", "")
	t.Setenv("POWERMEM_TOOL_EVENT_MAX_CHARS", "900")
	t.Setenv("POWERMEM_INFER_TOOL_EVENTS", "0")

	payload := map[string]any{
		"hook_event_name": "PostToolUse",
		"session_id":      "session-unit",
		"cwd":             "/workspace/project",
		"tool_name":       "Bash",
		"tool_use_id":     "tool-bash-unit",
		"duration_ms":     float64(321),
		"tool_input": map[string]any{
			"command": "pytest tests/unit --token " + unitSecret,
		},
		"tool_response": map[string]any{
			"exit_code": float64(0),
			"stdout":    "all good " + unitSecret,
			"stderr":    "",
		},
	}

	handoff := prepareToolEventHandoff(payload)
	if handoff == nil {
		t.Fatal("expected a prepared handoff")
	}
	if _, ok := handoff["tool_input"]; ok {
		t.Fatal("prepared handoff should not include raw tool_input")
	}
	if _, ok := handoff["tool_response"]; ok {
		t.Fatal("prepared handoff should not include raw tool_response")
	}
	requireNoUnitSecret(t, handoff)

	content, meta, runID, infer, ok := preparedToolEventPost(handoff)
	if !ok {
		t.Fatal("expected worker to read prepared post payload")
	}
	if infer {
		t.Fatal("tool event infer should default to false")
	}
	if runID != "session-unit" {
		t.Fatalf("unexpected run id: %q", runID)
	}
	if content == "" || !strings.Contains(content, "command_class=test") {
		t.Fatalf("unexpected content summary: %q", content)
	}
	if meta["event_id"] != "claude-code:session-unit:tool-bash-unit" {
		t.Fatalf("unexpected event id: %v", meta["event_id"])
	}
	if meta["command_class"] != "test" {
		t.Fatalf("unexpected command class: %v", meta["command_class"])
	}
	if meta["exit_code"] != int64(0) {
		t.Fatalf("unexpected exit code: %#v", meta["exit_code"])
	}
	if meta["duration_ms"] != int64(321) {
		t.Fatalf("unexpected duration: %#v", meta["duration_ms"])
	}
	requireNoUnitSecret(t, content)
	requireNoUnitSecret(t, meta)
}

func TestPrepareToolEventHandoffBlocksRawPayloadSecret(t *testing.T) {
	setHookScrubForTest(t, true)
	t.Setenv("POWERMEM_HOOK_SECRET_ACTION", "block")
	t.Setenv("POWERMEM_TOOL_SUCCESS_INCLUDE", "")
	t.Setenv("POWERMEM_TOOL_SUCCESS_EXCLUDE", "")

	handoff := prepareToolEventHandoff(map[string]any{
		"hook_event_name": "PostToolUse",
		"session_id":      "session-block",
		"cwd":             "/workspace/project",
		"tool_name":       "Bash",
		"tool_use_id":     "tool-block-unit",
		"tool_input": map[string]any{
			"command": "pytest tests/unit --token " + unitSecret,
		},
		"tool_response": map[string]any{
			"exit_code": float64(0),
			"stdout":    "ok",
		},
	})
	if handoff != nil {
		t.Fatalf("secret_action=block should skip handoff with raw payload secret: %#v", handoff)
	}
}

func TestPrepareMapPayloadHandoffScrubsBeforeWorkerPayloadFile(t *testing.T) {
	setHookScrubForTest(t, true)

	handoff := prepareMapPayloadHandoff(map[string]any{
		"hook_event_name":       "TaskCompleted",
		"session_id":            "session-lifecycle",
		"cwd":                   "/workspace/project",
		"transcript_path":       "/workspace/project/transcript.jsonl",
		"task_id":               "task-unit",
		"task_subject":          "Review handoff behavior",
		"task_description":      "Validate " + unitSecret + " is scrubbed before writing payload files.",
		"agent_transcript_path": "/workspace/project/agent-transcript.jsonl",
	}, scrubReport{})
	if handoff == nil {
		t.Fatal("expected scrubbed lifecycle handoff")
	}
	requireNoUnitSecret(t, handoff)
	b, err := json.Marshal(handoff)
	if err != nil {
		t.Fatalf("marshal handoff: %v", err)
	}
	if strings.Contains(string(b), "/workspace/project") {
		t.Fatalf("handoff leaked absolute path before worker payload file: %s", b)
	}
	if _, ok := handoff[preparedParentScrubReportKey]; !ok {
		t.Fatalf("expected parent scrub report in handoff: %#v", handoff)
	}
}

func TestPrepareMapPayloadHandoffBlocksBeforeWorkerPayloadFile(t *testing.T) {
	setHookScrubForTest(t, true)
	t.Setenv("POWERMEM_HOOK_SECRET_ACTION", "block")

	handoff := prepareMapPayloadHandoff(map[string]any{
		"hook_event_name":  "TaskCreated",
		"session_id":       "session-block",
		"task_id":          "task-block",
		"task_subject":     "Block raw payload secret",
		"task_description": "Do not write " + unitSecret + " to a worker payload file.",
	}, scrubReport{})
	if handoff != nil {
		t.Fatalf("secret_action=block should skip worker handoff before temp file write: %#v", handoff)
	}
}

func TestBuildToolEventPostCapturesAgentResponseLink(t *testing.T) {
	setHookScrubForTest(t, true)
	t.Setenv("POWERMEM_TOOL_SUCCESS_INCLUDE", "")
	t.Setenv("POWERMEM_TOOL_SUCCESS_EXCLUDE", "")

	content, meta, runID, infer, ok := buildToolEventPost(map[string]any{
		"session_id":  "session-agent",
		"cwd":         "/workspace/project",
		"tool_name":   "Agent",
		"tool_use_id": "tool-agent-unit",
		"tool_input": map[string]any{
			"agent_type":  "reviewer",
			"description": "Review hook metadata.",
			"prompt":      "Check for " + unitSecret,
		},
		"tool_response": map[string]any{
			"agentId":   "agent-unit-1",
			"agentType": "reviewer",
			"status":    "completed",
			"content": []any{
				map[string]any{"type": "text", "text": "Review completed with " + unitSecret},
			},
			"usage": map[string]any{"input_tokens": float64(12), "output_tokens": float64(5)},
		},
	})
	if !ok {
		t.Fatal("expected Agent tool event to be captured")
	}
	if infer {
		t.Fatal("Agent tool event infer should default to false")
	}
	if runID != "session-agent" {
		t.Fatalf("unexpected run id: %q", runID)
	}
	if meta["agent_id"] != "agent-unit-1" {
		t.Fatalf("expected response agent id, got %v", meta["agent_id"])
	}
	if meta["agent_type"] != "reviewer" {
		t.Fatalf("expected response agent type, got %v", meta["agent_type"])
	}
	if meta["agent_status"] != "completed" {
		t.Fatalf("expected response agent status, got %v", meta["agent_status"])
	}
	if !strings.Contains(meta["response_summary"].(string), "Review completed") {
		t.Fatalf("Agent response summary did not include content blocks: %v", meta["response_summary"])
	}
	if !strings.Contains(content, "Review completed") {
		t.Fatalf("content did not include Agent response summary: %q", content)
	}
	requireNoUnitSecret(t, content)
	requireNoUnitSecret(t, meta)
}

func TestBuildSessionStartQueryUsesBoundedMetadata(t *testing.T) {
	query := buildSessionStartQuery(map[string]any{
		"session_title": "No-LLM hook work",
		"source":        "startup",
		"agent_type":    "coder",
		"cwd":           "/workspace/project",
		"ignored":       "not included",
	})
	for _, want := range []string{
		"session_title: No-LLM hook work",
		"source: startup",
		"agent_type: coder",
		"cwd: /workspace/project",
	} {
		if !strings.Contains(query, want) {
			t.Fatalf("query %q did not include %q", query, want)
		}
	}
	if strings.Contains(query, "ignored") {
		t.Fatalf("query included unexpected metadata: %q", query)
	}
}

func TestSessionStartQueryScrubsPathBeforeSearch(t *testing.T) {
	setHookScrubForTest(t, true)
	query := buildSessionStartQuery(map[string]any{
		"session_title": "No-LLM hook work",
		"source":        "startup",
		"agent_type":    "coder",
		"cwd":           "/workspace/project",
	})
	scrubbed, ok := scrubPromptForSearch(query, loadHookPrivacyConfig())
	if !ok {
		t.Fatal("session start metadata without secrets should remain searchable")
	}
	if strings.Contains(scrubbed, "/workspace/project") {
		t.Fatalf("scrubbed query leaked absolute path: %q", scrubbed)
	}
	if !strings.Contains(scrubbed, "cwd: project") {
		t.Fatalf("scrubbed query did not keep a useful cwd basename: %q", scrubbed)
	}
}

func TestBuildToolFailurePostCapturesStructuredFailure(t *testing.T) {
	setHookScrubForTest(t, true)
	t.Setenv("POWERMEM_TOOL_FAILURE_MAX_CHARS", "900")
	t.Setenv("POWERMEM_INFER_TOOL_FAILURES", "0")

	content, meta, runID, infer, ok := buildToolFailurePost(map[string]any{
		"session_id":  "session-failure",
		"cwd":         "/workspace/project",
		"tool_name":   "Bash",
		"tool_use_id": "tool-failure-unit",
		"duration_ms": float64(456),
		"tool_input": map[string]any{
			"command": "pytest tests/unit --token " + unitSecret,
		},
		"tool_response": map[string]any{
			"exit_code": float64(2),
			"stderr":    "failed with " + unitSecret,
		},
		"error_type":    "non_zero_exit",
		"error_message": "pytest failed with " + unitSecret,
	})
	if !ok {
		t.Fatal("expected tool failure post")
	}
	if infer {
		t.Fatal("tool failure infer should default to false")
	}
	if runID != "session-failure" {
		t.Fatalf("unexpected run id: %q", runID)
	}
	if meta["event_name"] != "PostToolUseFailure" {
		t.Fatalf("unexpected event name: %v", meta["event_name"])
	}
	if meta["success"] != false {
		t.Fatalf("expected success=false, got %v", meta["success"])
	}
	if meta["command_class"] != "test" {
		t.Fatalf("unexpected command class: %v", meta["command_class"])
	}
	if meta["exit_code"] != int64(2) {
		t.Fatalf("unexpected exit code: %#v", meta["exit_code"])
	}
	requireNoUnitSecret(t, content)
	requireNoUnitSecret(t, meta)
}

func TestToolFailureInterruptsAreSkippedByDefaultInDispatcherGate(t *testing.T) {
	payload := map[string]any{
		"hook_event_name": "PostToolUseFailure",
		"tool_name":       "Bash",
		"is_interrupt":    true,
	}
	t.Setenv("POWERMEM_CAPTURE_INTERRUPTS", "")
	if !isInterruptPayload(payload) {
		t.Fatal("expected interrupt payload")
	}
	if captureInterrupts() {
		t.Fatal("interrupt capture should default to disabled")
	}
	t.Setenv("POWERMEM_CAPTURE_INTERRUPTS", "1")
	if !captureInterrupts() {
		t.Fatal("interrupt capture should be enabled by env")
	}
}

func TestBuildStopRollupPostBoundsAndScrubsFinalMessage(t *testing.T) {
	setHookScrubForTest(t, true)
	t.Setenv("POWERMEM_STOP_MAX_CHARS", "500")
	t.Setenv("POWERMEM_INFER_STOP", "0")

	content, meta, runID, infer, ok := buildStopRollupPost(map[string]any{
		"session_id":             "session-stop",
		"cwd":                    "/workspace/project",
		"last_assistant_message": "Implemented tests with " + unitSecret,
		"changed_files":          []any{"apps/claude-code-plugin/cmd/powermem-hook/main.go"},
		"background_tasks":       []any{"none"},
	})
	if !ok {
		t.Fatal("expected stop rollup")
	}
	if infer {
		t.Fatal("stop infer should default to false")
	}
	if runID != "session-stop" {
		t.Fatalf("unexpected run id: %q", runID)
	}
	if meta["kind"] != "stop-rollup" {
		t.Fatalf("unexpected kind: %v", meta["kind"])
	}
	if meta["event_name"] != "Stop" {
		t.Fatalf("unexpected event name: %v", meta["event_name"])
	}
	requireNoUnitSecret(t, content)
	requireNoUnitSecret(t, meta)

	_, _, _, _, ok = buildStopRollupPost(map[string]any{
		"session_id":             "session-empty-stop",
		"last_assistant_message": "   ",
	})
	if ok {
		t.Fatal("empty stop rollup should no-op")
	}
}

func TestBuildToolEventPostUnknownToolShapeOnlyWithWildcard(t *testing.T) {
	setHookScrubForTest(t, true)
	t.Setenv("POWERMEM_TOOL_SUCCESS_INCLUDE", "*")
	t.Setenv("POWERMEM_TOOL_SUCCESS_EXCLUDE", "")

	_, meta, _, _, ok := buildToolEventPost(map[string]any{
		"session_id":    "session-unknown",
		"tool_name":     "UnlistedTool",
		"tool_use_id":   "tool-unknown-unit",
		"tool_input":    []any{"raw", unitSecret},
		"tool_response": []any{"ok", unitSecret},
	})
	if !ok {
		t.Fatal("wildcard include should capture unknown tool")
	}
	if meta["input_summary"] != "shape=array[2]" {
		t.Fatalf("unexpected input summary: %v", meta["input_summary"])
	}
	if meta["response_summary"] != "shape=array[2]" {
		t.Fatalf("unexpected response summary: %v", meta["response_summary"])
	}
	requireNoUnitSecret(t, meta)
}

func TestReadTranscriptTailAppliesLineAndCharBounds(t *testing.T) {
	path := filepath.Join(t.TempDir(), "transcript.jsonl")
	lines := []string{
		`{"type":"user","message":{"content":"old context"}}`,
		`{"type":"assistant","message":{"content":"middle context"}}`,
		`{"type":"assistant","message":{"content":"recent context"}}`,
	}
	if err := os.WriteFile(path, []byte(strings.Join(lines, "\n")+"\n"), 0o600); err != nil {
		t.Fatalf("write transcript: %v", err)
	}

	snapshot, err := readTranscriptTail(path, 200, 1)
	if err != nil {
		t.Fatalf("read transcript tail: %v", err)
	}
	if !strings.Contains(snapshot.Text, "recent context") {
		t.Fatalf("tail did not include latest line: %q", snapshot.Text)
	}
	if strings.Contains(snapshot.Text, "old context") || strings.Contains(snapshot.Text, "middle context") {
		t.Fatalf("line bound was not applied: %q", snapshot.Text)
	}
	if snapshot.StartByte <= 0 {
		t.Fatalf("expected non-zero start offset, got %d", snapshot.StartByte)
	}
	st, err := os.Stat(path)
	if err != nil {
		t.Fatalf("stat transcript: %v", err)
	}
	if snapshot.EndByte != st.Size() {
		t.Fatalf("expected end offset %d, got %d", st.Size(), snapshot.EndByte)
	}

	snapshot, err = readTranscriptTail(path, 40, 10)
	if err != nil {
		t.Fatalf("read char-bounded transcript tail: %v", err)
	}
	if len(snapshot.Text) > 40 {
		t.Fatalf("char bound was not applied: len=%d text=%q", len(snapshot.Text), snapshot.Text)
	}
}

func TestEventKindUsesHookEventName(t *testing.T) {
	cases := map[string]string{
		"SubagentStop":  "subagent-stop",
		"TaskCompleted": "task-completed",
		"PreCompact":    "pre-compact",
	}
	for input, want := range cases {
		if got := eventKind(input); got != want {
			t.Fatalf("eventKind(%q) = %q, want %q", input, got, want)
		}
	}
}

func TestLifecycleContentCapturesTaskSchemaFields(t *testing.T) {
	content := lifecycleContent("TaskCompleted", "session-task", "/workspace/project", map[string]any{
		"task_id":          "task-001",
		"task_subject":     "Implement user authentication",
		"task_description": "Add login and signup endpoints",
		"teammate_name":    "implementer",
	})
	for _, want := range []string{
		"task_id: task-001",
		"task_subject: Implement user authentication",
		"task_description: Add login and signup endpoints",
		"teammate_name: implementer",
	} {
		if !strings.Contains(content, want) {
			t.Fatalf("lifecycle content %q did not include %q", content, want)
		}
	}
}

func TestBuildLifecycleEventPostUsesBoundedAllowlistedMetadata(t *testing.T) {
	setHookScrubForTest(t, true)
	longMessage := strings.Repeat("assistant-details-", 400) + unitSecret
	longDescription := strings.Repeat("task-description-", 300) + unitSecret

	content, meta, runID, infer, _, ok := buildLifecycleEventPost(map[string]any{
		"hook_event_name":        "SubagentStop",
		"session_id":             "session-lifecycle",
		"cwd":                    "/workspace/project",
		"task_id":                "task-001",
		"task_description":       longDescription,
		"last_assistant_message": longMessage,
		"background_tasks":       []any{longMessage},
		"token_usage": map[string]any{
			"input_tokens": float64(12),
			"raw_text":     longMessage,
		},
	}, scrubReport{})
	if !ok {
		t.Fatal("expected lifecycle event post")
	}
	if infer {
		t.Fatal("lifecycle infer should default to false")
	}
	if runID != "session-lifecycle" {
		t.Fatalf("unexpected run id: %q", runID)
	}
	if _, ok := meta["raw_payload"]; ok {
		t.Fatalf("lifecycle metadata should not include raw_payload: %#v", meta["raw_payload"])
	}
	if _, ok := meta["last_assistant_message"]; ok {
		t.Fatal("lifecycle metadata should not copy assistant message fields")
	}
	desc, ok := meta["task_description"].(string)
	if !ok || desc == "" {
		t.Fatalf("expected bounded task_description, got %#v", meta["task_description"])
	}
	if len(desc) > lifecycleDescriptionMaxChars+4 {
		t.Fatalf("task_description was not bounded: len=%d", len(desc))
	}
	usage, ok := meta["token_usage"].(map[string]any)
	if !ok {
		t.Fatalf("expected token usage map, got %#v", meta["token_usage"])
	}
	if usage["input_tokens"] != int64(12) {
		t.Fatalf("unexpected token usage: %#v", usage)
	}
	if _, ok := usage["raw_text"]; ok {
		t.Fatalf("token usage should only keep numeric token fields: %#v", usage)
	}
	requireNoUnitSecret(t, content)
	requireNoUnitSecret(t, meta)
}
