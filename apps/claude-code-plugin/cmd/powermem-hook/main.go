// powermem-hook: Claude Code hook stdin JSON -> PowerMem HTTP API.
// Cross-platform; zero runtime deps beyond the single binary.
package main

import (
	"bufio"
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"time"
)

// Default REST base when POWERMEM_BASE_URL is unset (matches .mcp.json local server).
const defaultPowerMemBaseURL = "http://localhost:8848"

const workerPayloadPathEnv = "POWERMEM_WORKER_PAYLOAD_PATH"
const preparedParentScrubReportKey = "_powermem_parent_scrub_report"
const lifecycleMetaMaxChars = 512
const lifecycleDescriptionMaxChars = 2000

type workerHandoffPayload struct {
	TranscriptPath    string `json:"transcript_path,omitempty"`
	SessionID         string `json:"session_id,omitempty"`
	CWD               string `json:"cwd,omitempty"`
	Reason            string `json:"reason,omitempty"`
	ParentScrubReport string `json:"parent_scrub_report,omitempty"`
}

func main() {
	if len(os.Args) >= 2 {
		switch os.Args[1] {
		case "worker-transcript":
			workerTranscript()
			return
		case "worker-compact":
			workerCompact()
			return
		case "worker-precompact":
			workerPreCompact()
			return
		case "worker-tool-event":
			workerToolEvent()
			return
		case "worker-lifecycle":
			workerLifecycleEvent()
			return
		case "worker-file":
			workerFile()
			return
		case "poll":
			runPollLoop()
			return
		}
	}
	stdinHook()
}

func baseURL() string {
	s := strings.TrimSpace(os.Getenv("POWERMEM_BASE_URL"))
	if s == "" {
		s = defaultPowerMemBaseURL
	}
	return strings.TrimRight(s, "/")
}

func spawnWorker(mode string, envExtra map[string]string) bool {
	self, err := os.Executable()
	if err != nil {
		return false
	}
	env := os.Environ()
	for k, v := range envExtra {
		env = append(env, k+"="+v)
	}
	cmd := exec.Command(self, mode)
	cmd.Env = env
	cmd.Stdin = nil
	cmd.Stdout = nil
	cmd.Stderr = nil
	setDetachedChild(cmd)
	return cmd.Start() == nil
}

func writeWorkerPayloadFile(payload any) (string, bool) {
	f, err := os.CreateTemp("", "powermem-hook-payload-*.json")
	if err != nil {
		return "", false
	}
	name := f.Name()
	encErr := json.NewEncoder(f).Encode(payload)
	closeErr := f.Close()
	if encErr != nil || closeErr != nil {
		_ = os.Remove(name)
		return "", false
	}
	return name, true
}

func spawnPayloadWorker(mode string, payload workerHandoffPayload) {
	path, ok := writeWorkerPayloadFile(payload)
	if !ok {
		return
	}
	if !spawnWorker(mode, map[string]string{workerPayloadPathEnv: path}) {
		_ = os.Remove(path)
	}
}

func spawnMapPayloadWorker(mode string, payload map[string]any) {
	path, ok := writeWorkerPayloadFile(payload)
	if !ok {
		return
	}
	if !spawnWorker(mode, map[string]string{workerPayloadPathEnv: path}) {
		_ = os.Remove(path)
	}
}

func readWorkerPayload() (workerHandoffPayload, bool) {
	path := strings.TrimSpace(os.Getenv(workerPayloadPathEnv))
	if path == "" {
		return workerHandoffPayload{}, false
	}
	b, err := os.ReadFile(path)
	_ = os.Remove(path)
	if err != nil {
		return workerHandoffPayload{}, false
	}
	var payload workerHandoffPayload
	if json.Unmarshal(b, &payload) != nil {
		return workerHandoffPayload{}, false
	}
	return payload, true
}

func readMapWorkerPayload() map[string]any {
	path := strings.TrimSpace(os.Getenv(workerPayloadPathEnv))
	if path == "" {
		return nil
	}
	b, err := os.ReadFile(path)
	_ = os.Remove(path)
	if err != nil {
		return nil
	}
	var payload map[string]any
	if json.Unmarshal(b, &payload) != nil {
		return nil
	}
	return payload
}

func stdinHook() {
	raw, err := io.ReadAll(os.Stdin)
	if err != nil || len(bytes.TrimSpace(raw)) == 0 {
		return
	}
	var payload map[string]any
	if json.Unmarshal(raw, &payload) != nil {
		return
	}

	event, _ := payload["hook_event_name"].(string)
	sid, _ := payload["session_id"].(string)
	cwd, _ := payload["cwd"].(string)

	switch event {
	case "SessionStart":
		handleSessionStart(payload)
	case "UserPromptSubmit":
		handleUserPromptSubmit(payload)
	case "SessionEnd":
		tp, _ := payload["transcript_path"].(string)
		if tp == "" {
			return
		}
		if st, err := os.Stat(tp); err != nil || st.IsDir() {
			return
		}
		reason, _ := payload["reason"].(string)
		cfg := loadHookPrivacyConfig()
		cwd, report, ok := scrubCWDForWorker(cwd, cfg)
		if !ok {
			return
		}
		parentReportRaw := ""
		if encoded := encodeScrubReport(report); encoded != "" {
			parentReportRaw = encoded
		}
		spawnPayloadWorker("worker-transcript", workerHandoffPayload{
			TranscriptPath:    tp,
			SessionID:         sid,
			CWD:               cwd,
			Reason:            reason,
			ParentScrubReport: parentReportRaw,
		})
	case "PostCompact":
		summary, _ := payload["compact_summary"].(string)
		if strings.TrimSpace(summary) == "" {
			return
		}
		if len(summary) > 900000 {
			summary = summary[:900000] + "\n…"
		}
		trigger, _ := payload["trigger"].(string)
		cfg := loadHookPrivacyConfig()
		summary, cwd, report, ok := scrubCompactForWorker(summary, cwd, cfg)
		if !ok {
			return
		}
		env := map[string]string{
			"POWERMEM_WORKER_COMPACT_SUMMARY": summary,
			"POWERMEM_WORKER_SESSION_ID":      sid,
			"POWERMEM_WORKER_CWD":             cwd,
			"POWERMEM_WORKER_TRIGGER":         trigger,
		}
		if encoded := encodeScrubReport(report); encoded != "" {
			env[parentScrubReportEnv] = encoded
		}
		spawnWorker("worker-compact", env)
	case "PreCompact":
		if !capturePreCompact() {
			return
		}
		tp, _ := payload["transcript_path"].(string)
		if tp == "" {
			return
		}
		if st, err := os.Stat(tp); err != nil || st.IsDir() {
			return
		}
		snapshot, err := readTranscriptTail(tp, maxPreCompactChars(), maxPreCompactLines())
		if err != nil || strings.TrimSpace(snapshot.Text) == "" {
			return
		}
		cfg := loadHookPrivacyConfig()
		scrubbedText, report := scrubText(snapshot.Text, cfg)
		if shouldBlockWrite(cfg, report) || strings.TrimSpace(scrubbedText) == "" {
			return
		}
		payload["_powermem_precompact_text"] = scrubbedText
		payload["_powermem_precompact_start_byte"] = snapshot.StartByte
		payload["_powermem_precompact_end_byte"] = snapshot.EndByte
		payload["_powermem_precompact_max_chars"] = maxPreCompactChars()
		payload["_powermem_precompact_max_lines"] = maxPreCompactLines()
		handoff := prepareMapPayloadHandoff(payload, report)
		if handoff == nil {
			return
		}
		spawnMapPayloadWorker("worker-precompact", handoff)
	case "PostToolUse":
		if !captureToolSuccess() {
			return
		}
		if !toolEventAllowed(toolNameFromPayload(payload)) {
			return
		}
		handoff := prepareToolEventHandoff(payload)
		if handoff == nil {
			return
		}
		spawnMapPayloadWorker("worker-tool-event", handoff)
	case "PostToolUseFailure":
		if !captureToolFailures() {
			return
		}
		if isInterruptPayload(payload) && !captureInterrupts() {
			return
		}
		handoff := prepareToolFailureHandoff(payload)
		if handoff == nil {
			return
		}
		spawnMapPayloadWorker("worker-tool-event", handoff)
	case "Stop":
		if !captureStopRollup() || boolField(payload, "stop_hook_active", false) {
			return
		}
		handoff := prepareStopRollupHandoff(payload)
		if handoff == nil {
			return
		}
		spawnMapPayloadWorker("worker-tool-event", handoff)
	case "SubagentStart", "SubagentStop":
		if !captureSubagents() {
			return
		}
		handoff := prepareMapPayloadHandoff(payload, scrubReport{})
		if handoff == nil {
			return
		}
		spawnMapPayloadWorker("worker-lifecycle", handoff)
	case "TaskCreated", "TaskCompleted":
		if !captureTasks() {
			return
		}
		handoff := prepareMapPayloadHandoff(payload, scrubReport{})
		if handoff == nil {
			return
		}
		spawnMapPayloadWorker("worker-lifecycle", handoff)
	}
}

func envInt(name string, def int, min int, max int) int {
	s := strings.TrimSpace(os.Getenv(name))
	if s == "" {
		return def
	}
	n, err := strconv.Atoi(s)
	if err != nil || n < min {
		return def
	}
	if max > 0 && n > max {
		return max
	}
	return n
}

func maxHookChars() int {
	s := strings.TrimSpace(os.Getenv("POWERMEM_HOOK_MAX_CHARS"))
	if s == "" {
		return 120000
	}
	n, err := strconv.Atoi(s)
	if err != nil || n < 500 {
		return 120000
	}
	return n
}

func inferTranscript() bool {
	switch strings.ToLower(strings.TrimSpace(os.Getenv("POWERMEM_INFER_TRANSCRIPT"))) {
	case "1", "true", "yes":
		return true
	default:
		return false
	}
}

func inferCompact() bool {
	switch strings.ToLower(strings.TrimSpace(os.Getenv("POWERMEM_INFER_COMPACT"))) {
	case "0", "false", "no":
		return false
	default:
		return true
	}
}

func capturePreCompact() bool {
	return envBool("POWERMEM_CAPTURE_PRECOMPACT", true)
}

func inferPreCompact() bool {
	return envBool("POWERMEM_INFER_PRECOMPACT", false)
}

func maxPreCompactChars() int {
	return envInt("POWERMEM_PRECOMPACT_MAX_CHARS", 120000, 500, 900000)
}

func maxPreCompactLines() int {
	return envInt("POWERMEM_PRECOMPACT_TAIL_LINES", 200, 1, 10000)
}

func captureToolSuccess() bool {
	return envBool("POWERMEM_CAPTURE_TOOL_SUCCESS", true)
}

func inferToolEvents() bool {
	return envBool("POWERMEM_INFER_TOOL_EVENTS", false)
}

func maxToolEventChars() int {
	return envInt("POWERMEM_TOOL_EVENT_MAX_CHARS", 6000, 500, 120000)
}

func captureToolFailures() bool {
	return envBool("POWERMEM_CAPTURE_TOOL_FAILURES", true)
}

func captureInterrupts() bool {
	return envBool("POWERMEM_CAPTURE_INTERRUPTS", false)
}

func maxToolFailureChars() int {
	return envInt("POWERMEM_TOOL_FAILURE_MAX_CHARS", 6000, 500, 120000)
}

func inferToolFailures() bool {
	return envBool("POWERMEM_INFER_TOOL_FAILURES", false)
}

func captureStopRollup() bool {
	return envBool("POWERMEM_CAPTURE_STOP_ROLLUP", false)
}

func maxStopChars() int {
	return envInt("POWERMEM_STOP_MAX_CHARS", 3000, 500, 120000)
}

func inferStop() bool {
	return envBool("POWERMEM_INFER_STOP", false)
}

func captureSubagents() bool {
	return envBool("POWERMEM_CAPTURE_SUBAGENTS", true)
}

func captureTasks() bool {
	return envBool("POWERMEM_CAPTURE_TASKS", true)
}

func inferLifecycleEvent(eventName string) bool {
	if envBool("POWERMEM_INFER_LIFECYCLE_EVENTS", false) {
		return true
	}
	switch eventName {
	case "SubagentStop":
		return envBool("POWERMEM_INFER_SUBAGENT_STOP", false)
	case "TaskCompleted":
		return envBool("POWERMEM_INFER_TASK_COMPLETED", false)
	default:
		return false
	}
}

func inferFile() bool {
	switch strings.ToLower(strings.TrimSpace(os.Getenv("POWERMEM_INFER_FILE"))) {
	case "1", "true", "yes":
		return true
	default:
		return false
	}
}

func splitCSVSet(raw string) map[string]bool {
	out := map[string]bool{}
	for _, part := range strings.Split(raw, ",") {
		item := strings.TrimSpace(part)
		if item != "" {
			out[item] = true
		}
	}
	return out
}

func defaultToolIncludeSet() map[string]bool {
	return map[string]bool{
		"Write":        true,
		"Edit":         true,
		"MultiEdit":    true,
		"Bash":         true,
		"Agent":        true,
		"ExitPlanMode": true,
	}
}

func toolEventAllowed(toolName string) bool {
	if toolName == "" {
		toolName = "unknown"
	}
	exclude := splitCSVSet(os.Getenv("POWERMEM_TOOL_SUCCESS_EXCLUDE"))
	if exclude["*"] || exclude[toolName] {
		return false
	}
	include := splitCSVSet(os.Getenv("POWERMEM_TOOL_SUCCESS_INCLUDE"))
	if len(include) == 0 {
		include = defaultToolIncludeSet()
	}
	return include["*"] || include[toolName]
}

func promptSearchEnabled() bool {
	switch strings.ToLower(strings.TrimSpace(os.Getenv("POWERMEM_PROMPT_SEARCH"))) {
	case "0", "false", "no", "off":
		return false
	default:
		return true
	}
}

func searchBodyUserID() string {
	return memoryUserID()
}

func searchBodyAgentID() string {
	return strings.TrimSpace(os.Getenv("POWERMEM_AGENT_ID"))
}

func promptSearchLimit() int {
	const defaultLimit = 8
	s := strings.TrimSpace(os.Getenv("POWERMEM_PROMPT_SEARCH_LIMIT"))
	if s == "" {
		return defaultLimit
	}
	n, err := strconv.Atoi(s)
	if err != nil || n < 1 {
		return defaultLimit
	}
	if n > 30 {
		return 30
	}
	return n
}

func promptSearchMaxContextChars() int {
	const defaultMax = 24000
	s := strings.TrimSpace(os.Getenv("POWERMEM_PROMPT_SEARCH_MAX_CHARS"))
	if s == "" {
		return defaultMax
	}
	n, err := strconv.Atoi(s)
	if err != nil || n < 500 {
		return defaultMax
	}
	return n
}

func sessionStartSearchEnabled() bool {
	return envBool("POWERMEM_SESSION_START_SEARCH", true)
}

func sessionStartLimit() int {
	return envInt("POWERMEM_SESSION_START_LIMIT", 6, 1, 30)
}

func sessionStartMaxContextChars() int {
	return envInt("POWERMEM_SESSION_START_MAX_CHARS", 16000, 500, 120000)
}

func buildSessionStartQuery(payload map[string]any) string {
	parts := []string{}
	for _, key := range []string{"session_title", "source", "agent_type", "cwd"} {
		if value := stringField(payload, key); value != "" {
			parts = append(parts, key+": "+value)
		}
	}
	return strings.Join(parts, "\n")
}

func handleSessionStart(payload map[string]any) {
	if !sessionStartSearchEnabled() {
		return
	}
	query := strings.TrimSpace(buildSessionStartQuery(payload))
	if query == "" {
		return
	}
	cfg := loadHookPrivacyConfig()
	var ok bool
	query, ok = scrubPromptForSearch(query, cfg)
	if !ok {
		return
	}
	ctx, err := searchMemories(query, sessionStartLimit())
	if err != nil || strings.TrimSpace(ctx) == "" {
		return
	}
	if cfg.Enabled {
		var report scrubReport
		ctx, report = scrubText(ctx, cfg)
		if shouldBlockWrite(cfg, report) || strings.TrimSpace(ctx) == "" {
			return
		}
	}
	maxC := sessionStartMaxContextChars()
	if len(ctx) > maxC {
		ctx = ctx[:maxC] + "\n…"
	}
	out := map[string]any{
		"hookSpecificOutput": map[string]any{
			"hookEventName":     "SessionStart",
			"additionalContext": ctx,
		},
	}
	b, err := json.Marshal(out)
	if err != nil {
		return
	}
	_, _ = os.Stdout.Write(b)
}

func handleUserPromptSubmit(payload map[string]any) {
	if !promptSearchEnabled() {
		return
	}
	prompt, _ := payload["prompt"].(string)
	prompt = strings.TrimSpace(prompt)
	if len(prompt) < 2 {
		return
	}
	cfg := loadHookPrivacyConfig()
	var ok bool
	prompt, ok = scrubPromptForSearch(prompt, cfg)
	if !ok {
		return
	}
	ctx, err := searchMemoriesForPrompt(prompt)
	if err != nil || strings.TrimSpace(ctx) == "" {
		return
	}
	if cfg.Enabled {
		var report scrubReport
		ctx, report = scrubText(ctx, cfg)
		if shouldBlockWrite(cfg, report) || strings.TrimSpace(ctx) == "" {
			return
		}
	}
	maxC := promptSearchMaxContextChars()
	if len(ctx) > maxC {
		ctx = ctx[:maxC] + "\n…"
	}
	out := map[string]any{
		"hookSpecificOutput": map[string]any{
			"hookEventName":     "UserPromptSubmit",
			"additionalContext": ctx,
		},
	}
	b, err := json.Marshal(out)
	if err != nil {
		return
	}
	_, _ = os.Stdout.Write(b)
}

func searchMemoriesForPrompt(query string) (string, error) {
	return searchMemories(query, promptSearchLimit())
}

func searchMemories(query string, limit int) (string, error) {
	base := baseURL()
	body := map[string]any{
		"query": query,
		"limit": limit,
	}
	userID := searchBodyUserID()
	agentID := searchBodyAgentID()
	cfg := loadHookPrivacyConfig()
	if cfg.Enabled {
		var report scrubReport
		userID, report = scrubSearchIdentifier(userID, cfg)
		if cfg.SearchSecretPolicy == "skip" && report.SecretRedactions > 0 {
			return "", nil
		}
		agentID, report = scrubSearchIdentifier(agentID, cfg)
		if cfg.SearchSecretPolicy == "skip" && report.SecretRedactions > 0 {
			return "", nil
		}
	}
	if u := userID; u != "" {
		body["user_id"] = u
	}
	if a := agentID; a != "" {
		body["agent_id"] = a
	}
	b, err := json.Marshal(body)
	if err != nil {
		return "", err
	}
	req, err := http.NewRequest(http.MethodPost, base+"/api/v1/memories/search", bytes.NewReader(b))
	if err != nil {
		return "", err
	}
	req.Header.Set("Content-Type", "application/json; charset=utf-8")
	if k := strings.TrimSpace(os.Getenv("POWERMEM_API_KEY")); k != "" {
		req.Header.Set("X-API-Key", k)
	}
	c := &http.Client{Timeout: 90 * time.Second}
	resp, err := c.Do(req)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()
	respBody, err := io.ReadAll(resp.Body)
	if err != nil {
		return "", err
	}
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return "", fmt.Errorf("search http %d", resp.StatusCode)
	}
	return formatSearchResults(respBody)
}

func formatSearchResults(respBody []byte) (string, error) {
	var root map[string]any
	if json.Unmarshal(respBody, &root) != nil {
		return "", fmt.Errorf("invalid search json")
	}
	data, _ := root["data"].(map[string]any)
	if data == nil {
		return "", nil
	}
	results, _ := data["results"].([]any)
	if len(results) == 0 {
		return "", nil
	}
	var b strings.Builder
	b.WriteString("## PowerMem (retrieved for this context)\n\nRelevant long-term memories from PowerMem; use if they help with the current Claude Code context. Ignore if unrelated.\n\n")
	for i, el := range results {
		m, ok := el.(map[string]any)
		if !ok {
			continue
		}
		content, _ := m["content"].(string)
		content = strings.TrimSpace(content)
		if content == "" {
			continue
		}
		score, _ := m["score"].(float64)
		b.WriteString(fmt.Sprintf("### Memory %d", i+1))
		if score > 0 {
			b.WriteString(fmt.Sprintf(" (score %.2f)", score))
		}
		b.WriteString("\n\n")
		b.WriteString(content)
		b.WriteString("\n\n")
	}
	s := strings.TrimSpace(b.String())
	if s == "" {
		return "", nil
	}
	return s, nil
}

func scrubSearchIdentifier(value string, cfg hookPrivacyConfig) (string, scrubReport) {
	if cfg.SearchSecretPolicy == "off" {
		return scrubTextWithoutPromptSecretPolicy(value, cfg)
	}
	return scrubMetadataString("search_id", value, cfg)
}

func postMemory(content string, meta map[string]any, runID *string, infer bool) error {
	return postMemoryWithScrubReport(content, meta, runID, infer, scrubReport{})
}

func postMemoryWithScrubReport(content string, meta map[string]any, runID *string, infer bool, parentReport scrubReport) error {
	cfg := loadHookPrivacyConfig()
	userID := memoryUserID()
	agentID := strings.TrimSpace(os.Getenv("POWERMEM_AGENT_ID"))
	runIDValue := ""
	if runID != nil {
		runIDValue = *runID
	}
	if cfg.Enabled {
		var report scrubReport
		userID, report = scrubMetadataString("user_id", userID, cfg)
		parentReport.merge(report)
		agentID, report = scrubMetadataString("agent_id", agentID, cfg)
		parentReport.merge(report)
		runIDValue, report = scrubMetadataString("run_id", runIDValue, cfg)
		parentReport.merge(report)
		if shouldBlockWrite(cfg, parentReport) {
			return nil
		}
	}
	content, meta, blocked := scrubMemoryPayload(content, meta, cfg, parentReport)
	if blocked {
		return nil
	}
	return postMemoryRaw(content, meta, userID, agentID, runIDValue, infer)
}

func memoryUserID() string {
	if u := strings.TrimSpace(os.Getenv("POWERMEM_USER_ID")); u != "" {
		return u
	}
	if u := os.Getenv("USER"); u != "" {
		return u
	}
	return os.Getenv("USERNAME")
}

func postMemoryRaw(content string, meta map[string]any, userID string, agentID string, runID string, infer bool) error {
	base := baseURL()
	body := map[string]any{
		"content":  content,
		"infer":    infer,
		"metadata": meta,
	}
	if userID != "" {
		body["user_id"] = userID
	}
	if agentID != "" {
		body["agent_id"] = agentID
	}
	if runID != "" {
		body["run_id"] = runID
	}
	b, err := json.Marshal(body)
	if err != nil {
		return err
	}
	req, err := http.NewRequest(http.MethodPost, base+"/api/v1/memories", bytes.NewReader(b))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json; charset=utf-8")
	if k := strings.TrimSpace(os.Getenv("POWERMEM_API_KEY")); k != "" {
		req.Header.Set("X-API-Key", k)
	}
	c := &http.Client{Timeout: 120 * time.Second}
	resp, err := c.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	_, _ = io.Copy(io.Discard, resp.Body)
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return fmt.Errorf("http %d", resp.StatusCode)
	}
	return nil
}

func workerTranscript() {
	path := os.Getenv("POWERMEM_WORKER_TRANSCRIPT_PATH")
	sid := os.Getenv("POWERMEM_WORKER_SESSION_ID")
	cwd := os.Getenv("POWERMEM_WORKER_CWD")
	reason := os.Getenv("POWERMEM_WORKER_REASON")
	parentReportRaw := os.Getenv(parentScrubReportEnv)
	if payload, ok := readWorkerPayload(); ok {
		if payload.TranscriptPath != "" {
			path = payload.TranscriptPath
		}
		if payload.SessionID != "" {
			sid = payload.SessionID
		}
		if payload.CWD != "" {
			cwd = payload.CWD
		}
		if payload.Reason != "" {
			reason = payload.Reason
		}
		if payload.ParentScrubReport != "" {
			parentReportRaw = payload.ParentScrubReport
		}
	}
	if path == "" {
		return
	}
	text, err := readTranscriptText(path, maxHookChars())
	if err != nil || strings.TrimSpace(text) == "" {
		return
	}
	header := fmt.Sprintf("Claude Code session transcript (session_id=%s, cwd=%s, reason=%s)\n\n", sid, cwd, reason)
	runID := sid
	if err := postMemoryWithScrubReport(header+text, map[string]any{
		"source":             "claude-code-hook",
		"kind":               "session-end-transcript",
		"transcript_path":    path,
		"session_id":         sid,
		"cwd":                cwd,
		"session_end_reason": reason,
	}, &runID, inferTranscript(), decodeScrubReport(parentReportRaw)); err != nil {
		os.Exit(1)
	}
}

func workerCompact() {
	summary := strings.TrimSpace(os.Getenv("POWERMEM_WORKER_COMPACT_SUMMARY"))
	if summary == "" {
		return
	}
	sid := os.Getenv("POWERMEM_WORKER_SESSION_ID")
	cwd := os.Getenv("POWERMEM_WORKER_CWD")
	trigger := os.Getenv("POWERMEM_WORKER_TRIGGER")
	runID := sid
	content := fmt.Sprintf("Claude Code context compact summary (session_id=%s, cwd=%s, trigger=%s)\n\n%s", sid, cwd, trigger, summary)
	parentReport := decodeScrubReport(os.Getenv(parentScrubReportEnv))
	if err := postMemoryWithScrubReport(content, map[string]any{
		"source":          "claude-code-hook",
		"kind":            "post-compact-summary",
		"session_id":      sid,
		"cwd":             cwd,
		"compact_trigger": trigger,
	}, &runID, inferCompact(), parentReport); err != nil {
		os.Exit(1)
	}
}

type transcriptTailSnapshot struct {
	Text      string
	StartByte int64
	EndByte   int64
}

func transcriptFingerprint(path string) string {
	abs, err := filepath.Abs(path)
	if err != nil {
		abs = path
	}
	sum := sha256.Sum256([]byte(abs))
	return hex.EncodeToString(sum[:])[:16]
}

func readTranscriptTail(path string, maxChars int, maxLines int) (transcriptTailSnapshot, error) {
	st, err := os.Stat(path)
	if err != nil {
		return transcriptTailSnapshot{}, err
	}
	size := st.Size()
	if size <= 0 {
		return transcriptTailSnapshot{}, nil
	}
	window := int64(maxChars*4 + 64*1024)
	if window < int64(maxChars) {
		window = int64(maxChars)
	}
	start := int64(0)
	if size > window {
		start = size - window
	}
	f, err := os.Open(path)
	if err != nil {
		return transcriptTailSnapshot{}, err
	}
	defer f.Close()
	if _, err := f.Seek(start, io.SeekStart); err != nil {
		return transcriptTailSnapshot{}, err
	}
	data, err := io.ReadAll(f)
	if err != nil {
		return transcriptTailSnapshot{}, err
	}
	if start > 0 {
		if idx := bytes.IndexByte(data, '\n'); idx >= 0 && idx+1 < len(data) {
			start += int64(idx + 1)
			data = data[idx+1:]
		}
	}
	if maxLines > 0 {
		lineStarts := []int{0}
		for i, b := range data {
			if b == '\n' && i+1 < len(data) {
				lineStarts = append(lineStarts, i+1)
			}
		}
		if len(lineStarts) > maxLines {
			lineStart := lineStarts[len(lineStarts)-maxLines]
			start += int64(lineStart)
			data = data[lineStart:]
		}
	}
	if len(data) > maxChars {
		trim := len(data) - maxChars
		if idx := bytes.IndexByte(data[trim:], '\n'); idx >= 0 && trim+idx+1 < len(data) {
			trim += idx + 1
		}
		start += int64(trim)
		data = data[trim:]
	}
	text := strings.TrimSpace(string(data))
	if text == "" {
		return transcriptTailSnapshot{}, nil
	}
	return transcriptTailSnapshot{Text: text, StartByte: start, EndByte: size}, nil
}

func workerPreCompact() {
	payload := readMapWorkerPayload()
	if payload == nil {
		return
	}
	parentReport := decodeScrubReport(stringField(payload, preparedParentScrubReportKey))
	delete(payload, preparedParentScrubReportKey)
	path := stringField(payload, "transcript_path")
	if path == "" {
		return
	}
	text := stringField(payload, "_powermem_precompact_text")
	if strings.TrimSpace(text) == "" {
		return
	}
	startByte := int64Field(payload, "_powermem_precompact_start_byte")
	endByte := int64Field(payload, "_powermem_precompact_end_byte")
	maxC := intField(payload, "_powermem_precompact_max_chars", maxPreCompactChars())
	maxL := intField(payload, "_powermem_precompact_max_lines", maxPreCompactLines())
	sid := stringField(payload, "session_id")
	cwd := stringField(payload, "cwd")
	trigger := stringField(payload, "trigger")
	runID := sid
	content := fmt.Sprintf("Claude Code pre-compact context snapshot (session_id=%s, cwd=%s, trigger=%s)\n\n%s", sid, cwd, trigger, text)
	meta := map[string]any{
		"source":                      "claude-code-hook",
		"kind":                        "pre-compact-snapshot",
		"event_name":                  "PreCompact",
		"session_id":                  sid,
		"cwd":                         cwd,
		"compact_trigger":             trigger,
		"transcript_path":             path,
		"transcript_path_fingerprint": transcriptFingerprint(path),
		"start_byte_offset":           startByte,
		"end_byte_offset":             endByte,
		"max_chars":                   maxC,
		"max_lines":                   maxL,
		"schema_version":              1,
		"scrub_mode":                  hookScrubEnabled(),
		"infer_mode":                  inferPreCompact(),
	}
	if custom := stringField(payload, "custom_instructions"); custom != "" {
		meta["custom_instructions"] = custom
	}
	if err := postMemoryWithScrubReport(content, meta, &runID, inferPreCompact(), parentReport); err != nil {
		os.Exit(1)
	}
}

func prepareToolEventHandoff(payload map[string]any) map[string]any {
	parentReport, ok := scrubPayloadReportForHandoff(payload)
	if !ok {
		return nil
	}
	content, meta, runID, infer, ok := buildToolEventPost(payload)
	if !ok {
		return nil
	}
	return prepareMemoryPostHandoffWithReport(content, meta, runID, infer, parentReport)
}

func prepareMemoryPostHandoff(content string, meta map[string]any, runID string, infer bool) map[string]any {
	return prepareMemoryPostHandoffWithReport(content, meta, runID, infer, scrubReport{})
}

func prepareMemoryPostHandoffWithReport(content string, meta map[string]any, runID string, infer bool, parentReport scrubReport) map[string]any {
	if strings.TrimSpace(content) == "" || meta == nil {
		return nil
	}
	if shouldBlockWrite(loadHookPrivacyConfig(), parentReport) {
		return nil
	}
	scrubbedContent, contentReport, ok := scrubTextForHandoffWithReport(content)
	if !ok || strings.TrimSpace(scrubbedContent) == "" {
		return nil
	}
	parentReport.merge(contentReport)
	scrubbedRunID, runIDReport, ok := scrubTextForHandoffWithReport(runID)
	if !ok {
		return nil
	}
	parentReport.merge(runIDReport)
	scrubbedMeta, metaReport, ok := scrubValueForHandoffWithReport(meta)
	if !ok {
		return nil
	}
	parentReport.merge(metaReport)
	handoff := map[string]any{
		"_powermem_content":  scrubbedContent,
		"_powermem_metadata": scrubbedMeta,
		"_powermem_run_id":   scrubbedRunID,
		"_powermem_infer":    infer,
	}
	if encoded := encodeScrubReport(parentReport); encoded != "" {
		handoff[preparedParentScrubReportKey] = encoded
	}
	return handoff
}

func prepareToolFailureHandoff(payload map[string]any) map[string]any {
	parentReport, ok := scrubPayloadReportForHandoff(payload)
	if !ok {
		return nil
	}
	content, meta, runID, infer, ok := buildToolFailurePost(payload)
	if !ok {
		return nil
	}
	return prepareMemoryPostHandoffWithReport(content, meta, runID, infer, parentReport)
}

func prepareStopRollupHandoff(payload map[string]any) map[string]any {
	parentReport, ok := scrubPayloadReportForHandoff(payload)
	if !ok {
		return nil
	}
	content, meta, runID, infer, ok := buildStopRollupPost(payload)
	if !ok {
		return nil
	}
	return prepareMemoryPostHandoffWithReport(content, meta, runID, infer, parentReport)
}

func preparedToolEventPost(payload map[string]any) (string, map[string]any, string, bool, bool) {
	content := stringField(payload, "_powermem_content")
	meta := nestedMap(payload["_powermem_metadata"])
	if content == "" || meta == nil {
		return "", nil, "", false, false
	}
	runID := stringField(payload, "_powermem_run_id")
	infer := boolField(payload, "_powermem_infer", inferToolEvents())
	return content, meta, runID, infer, true
}

func isInterruptPayload(payload map[string]any) bool {
	if payload == nil {
		return false
	}
	if boolField(payload, "is_interrupt", false) || boolField(payload, "interrupted", false) {
		return true
	}
	raw := strings.ToLower(firstString(payload, "error_type", "status", "reason"))
	return strings.Contains(raw, "interrupt")
}

func boolField(m map[string]any, key string, def bool) bool {
	if m == nil {
		return def
	}
	switch x := m[key].(type) {
	case bool:
		return x
	case string:
		return envBoolValue(x, def)
	default:
		return def
	}
}

func envBool(name string, def bool) bool {
	return envBoolValue(os.Getenv(name), def)
}

func envBoolValue(raw string, def bool) bool {
	switch strings.ToLower(strings.TrimSpace(raw)) {
	case "1", "true", "yes", "on":
		return true
	case "0", "false", "no", "off":
		return false
	default:
		return def
	}
}

func hookScrubEnabled() bool {
	return loadHookPrivacyConfig().Enabled
}

func scrubTextForHandoff(s string) (string, bool) {
	out, _, ok := scrubTextForHandoffWithReport(s)
	return out, ok
}

func scrubTextForHandoffWithReport(s string) (string, scrubReport, bool) {
	cfg := loadHookPrivacyConfig()
	if !cfg.Enabled {
		return s, scrubReport{}, true
	}
	out, report := scrubText(s, cfg)
	if shouldBlockWrite(cfg, report) {
		return "", report, false
	}
	return out, report, true
}

func scrubValueForHandoff(v any) any {
	out, _, ok := scrubValueForHandoffWithReport(v)
	if !ok {
		return nil
	}
	return out
}

func scrubValueForHandoffWithReport(v any) (any, scrubReport, bool) {
	cfg := loadHookPrivacyConfig()
	if !cfg.Enabled {
		return v, scrubReport{}, true
	}
	scrubbed, report := scrubMetadataValue("payload", v, cfg)
	if shouldBlockWrite(cfg, report) {
		return nil, report, false
	}
	return scrubbed, report, true
}

func scrubPayloadReportForHandoff(v any) (scrubReport, bool) {
	cfg := loadHookPrivacyConfig()
	if !cfg.Enabled {
		return scrubReport{}, true
	}
	_, report := scrubMetadataValue("payload", v, cfg)
	if shouldBlockWrite(cfg, report) {
		return report, false
	}
	return report, true
}

func prepareMapPayloadHandoff(payload map[string]any, parentReport scrubReport) map[string]any {
	if payload == nil {
		return nil
	}
	if shouldBlockWrite(loadHookPrivacyConfig(), parentReport) {
		return nil
	}
	scrubbed, report, ok := scrubValueForHandoffWithReport(payload)
	if !ok {
		return nil
	}
	parentReport.merge(report)
	handoff, ok := scrubbed.(map[string]any)
	if !ok {
		return nil
	}
	if encoded := encodeScrubReport(parentReport); encoded != "" {
		handoff[preparedParentScrubReportKey] = encoded
	}
	return handoff
}

func buildToolEventPost(payload map[string]any) (string, map[string]any, string, bool, bool) {
	if payload == nil {
		return "", nil, "", false, false
	}
	toolName := toolNameFromPayload(payload)
	if toolName == "" {
		toolName = "unknown"
	}
	if !toolEventAllowed(toolName) {
		return "", nil, "", false, false
	}
	sid := stringField(payload, "session_id")
	cwd := stringField(payload, "cwd")
	toolUseID := firstString(payload, "tool_use_id", "toolUseID", "toolUseId", "id")
	input := firstAny(payload, "tool_input", "input", "toolInput")
	response := firstAny(payload, "tool_response", "response", "toolResponse", "result")
	maxC := maxToolEventChars()
	inputSummary := summarizeToolInput(toolName, input, maxC/2)
	responseSummary := summarizeToolResponse(toolName, response, maxC/2)
	paths := extractPaths(input, 20)
	eventID := ""
	if sid != "" && toolUseID != "" {
		eventID = "claude-code:" + sid + ":" + toolUseID
	}
	status := firstString(payload, "status")
	if status == "" {
		status = "success"
	}
	content := fmt.Sprintf("Claude Code tool event (tool=%s, status=%s, session_id=%s, cwd=%s)\n\nInput summary:\n%s\n\nResponse summary:\n%s", toolName, status, sid, cwd, inputSummary, responseSummary)
	if len(content) > maxC {
		content = content[:maxC] + "\n..."
	}
	runID := sid
	meta := map[string]any{
		"source":           "claude-code-hook",
		"kind":             "post-tool-use",
		"event_name":       "PostToolUse",
		"event_id":         eventID,
		"session_id":       sid,
		"cwd":              cwd,
		"tool_name":        toolName,
		"tool_use_id":      toolUseID,
		"status":           status,
		"success":          true,
		"input_summary":    inputSummary,
		"response_summary": responseSummary,
		"affected_paths":   paths,
		"schema_version":   1,
		"scrub_mode":       hookScrubEnabled(),
		"infer_mode":       inferToolEvents(),
	}
	if toolName == "Agent" {
		if responseMap := nestedMap(response); responseMap != nil {
			copyStringMeta(meta, responseMap, "agent_id", "agentId", "agent_id", "subagent_id", "subagentId")
			copyStringMeta(meta, responseMap, "agent_type", "agentType", "agent_type", "subagent_type", "subagentType")
			copyStringMeta(meta, responseMap, "agent_status", "status")
			if usage := firstAny(responseMap, "token_usage", "tokenUsage", "usage"); usage != nil {
				meta["token_usage"] = scrubValueForHandoff(usage)
			}
		}
	}
	if commandClass := classifyCommand(commandFromToolInput(input)); commandClass != "" {
		meta["command_class"] = commandClass
	}
	if exitCode, ok := numericField(response, "exit_code", "exitCode", "code"); ok {
		meta["exit_code"] = exitCode
	}
	if duration, ok := numericField(payload, "duration_ms", "durationMs", "duration"); ok {
		meta["duration_ms"] = duration
	}
	return content, meta, runID, inferToolEvents(), true
}

func buildToolFailurePost(payload map[string]any) (string, map[string]any, string, bool, bool) {
	if payload == nil {
		return "", nil, "", false, false
	}
	toolName := toolNameFromPayload(payload)
	if toolName == "" {
		toolName = "unknown"
	}
	sid := stringField(payload, "session_id")
	cwd := stringField(payload, "cwd")
	toolUseID := firstString(payload, "tool_use_id", "toolUseID", "toolUseId", "id")
	input := firstAny(payload, "tool_input", "input", "toolInput")
	response := firstAny(payload, "tool_response", "response", "toolResponse", "result")
	errorType := firstString(payload, "error_type", "errorType", "status", "reason")
	errorMessage := firstString(payload, "error_message", "errorMessage", "message", "error")
	if errorMessage == "" {
		errorMessage = textField(nestedMap(response), "stderr")
	}
	if errorMessage == "" {
		errorMessage = textFromAny(response)
	}
	if toolName == "unknown" && sid == "" && toolUseID == "" && strings.TrimSpace(errorMessage) == "" {
		return "", nil, "", false, false
	}
	maxC := maxToolFailureChars()
	inputSummary := summarizeToolInput(toolName, input, maxC/2)
	errorSummary := truncateText(errorMessage, maxC/2)
	if errorSummary == "" {
		errorSummary = "shape=" + valueShape(response)
	}
	paths := extractPaths(input, 20)
	status := "failure"
	content := fmt.Sprintf("Claude Code tool failure (tool=%s, status=%s, session_id=%s, cwd=%s)\n\nInput summary:\n%s\n\nError summary:\n%s", toolName, status, sid, cwd, inputSummary, errorSummary)
	if len(content) > maxC {
		content = content[:maxC] + "\n..."
	}
	runID := sid
	meta := map[string]any{
		"source":         "claude-code-hook",
		"kind":           "post-tool-use-failure",
		"event_name":     "PostToolUseFailure",
		"session_id":     sid,
		"cwd":            cwd,
		"tool_name":      toolName,
		"tool_use_id":    toolUseID,
		"status":         status,
		"success":        false,
		"is_interrupt":   isInterruptPayload(payload),
		"error_type":     errorType,
		"error_message":  errorSummary,
		"input_summary":  inputSummary,
		"affected_paths": paths,
		"schema_version": 1,
		"scrub_mode":     hookScrubEnabled(),
		"infer_mode":     inferToolFailures(),
	}
	if commandClass := classifyCommand(commandFromToolInput(input)); commandClass != "" {
		meta["command_class"] = commandClass
	}
	if exitCode, ok := numericField(response, "exit_code", "exitCode", "code"); ok {
		meta["exit_code"] = exitCode
	}
	if duration, ok := numericField(payload, "duration_ms", "durationMs", "duration"); ok {
		meta["duration_ms"] = duration
	}
	return content, meta, runID, inferToolFailures(), true
}

func buildStopRollupPost(payload map[string]any) (string, map[string]any, string, bool, bool) {
	if payload == nil {
		return "", nil, "", false, false
	}
	sid := stringField(payload, "session_id")
	cwd := stringField(payload, "cwd")
	finalMessage := firstString(payload, "last_assistant_message", "assistant_message", "message", "summary")
	if finalMessage == "" {
		finalMessage = textField(payload, "transcript_tail")
	}
	finalMessage = truncateText(finalMessage, maxStopChars())
	if strings.TrimSpace(finalMessage) == "" {
		return "", nil, "", false, false
	}
	changed := firstAny(payload, "changed_files", "edited_files", "affected_paths")
	background := firstAny(payload, "background_tasks", "session_crons")
	runID := sid
	content := fmt.Sprintf("Claude Code stop rollup (session_id=%s, cwd=%s)\n\nFinal assistant message preview:\n%s", sid, cwd, finalMessage)
	meta := map[string]any{
		"source":           "claude-code-hook",
		"kind":             "stop-rollup",
		"event_name":       "Stop",
		"session_id":       sid,
		"cwd":              cwd,
		"success":          true,
		"stop_hook_active": boolField(payload, "stop_hook_active", false),
		"schema_version":   1,
		"scrub_mode":       hookScrubEnabled(),
		"infer_mode":       inferStop(),
	}
	if changed != nil {
		meta["changed_files"] = scrubValueForHandoff(changed)
	}
	if background != nil {
		meta["background_tasks"] = scrubValueForHandoff(background)
	}
	return content, meta, runID, inferStop(), true
}

func workerToolEvent() {
	payload := readMapWorkerPayload()
	if payload == nil {
		return
	}
	parentReport := decodeScrubReport(stringField(payload, preparedParentScrubReportKey))
	content, meta, runID, infer, ok := preparedToolEventPost(payload)
	if !ok {
		content, meta, runID, infer, ok = buildToolEventPost(payload)
	}
	if !ok {
		return
	}
	if err := postMemoryWithScrubReport(content, meta, &runID, infer, parentReport); err != nil {
		os.Exit(1)
	}
}

func workerLifecycleEvent() {
	payload := readMapWorkerPayload()
	if payload == nil {
		return
	}
	parentReport := decodeScrubReport(stringField(payload, preparedParentScrubReportKey))
	delete(payload, preparedParentScrubReportKey)
	content, meta, runID, infer, parentReport, ok := buildLifecycleEventPost(payload, parentReport)
	if !ok {
		return
	}
	if err := postMemoryWithScrubReport(content, meta, &runID, infer, parentReport); err != nil {
		os.Exit(1)
	}
}

func buildLifecycleEventPost(payload map[string]any, parentReport scrubReport) (string, map[string]any, string, bool, scrubReport, bool) {
	eventName := stringField(payload, "hook_event_name")
	if eventName == "" {
		return "", nil, "", false, parentReport, false
	}
	sid := stringField(payload, "session_id")
	cwd := stringField(payload, "cwd")
	runID := sid
	kind := eventKind(eventName)
	content := lifecycleContent(eventName, sid, cwd, payload)
	infer := inferLifecycleEvent(eventName)
	meta := map[string]any{
		"source":         "claude-code-hook",
		"kind":           kind,
		"event_name":     eventName,
		"session_id":     sid,
		"cwd":            cwd,
		"schema_version": 1,
		"scrub_mode":     hookScrubEnabled(),
		"infer_mode":     infer,
	}
	copyBoundedStringMeta(meta, payload, "agent_id", lifecycleMetaMaxChars, "agent_id", "agentId", "subagent_id", "subagentId")
	copyBoundedStringMeta(meta, payload, "agent_type", lifecycleMetaMaxChars, "agent_type", "agentType", "subagent_type", "subagentType")
	copyBoundedStringMeta(meta, payload, "task_id", lifecycleMetaMaxChars, "task_id", "taskId")
	copyBoundedStringMeta(meta, payload, "task_subject", lifecycleMetaMaxChars, "task_subject", "taskSubject")
	copyBoundedStringMeta(meta, payload, "task_description", lifecycleDescriptionMaxChars, "task_description", "taskDescription")
	copyBoundedStringMeta(meta, payload, "teammate_name", lifecycleMetaMaxChars, "teammate_name", "teammateName")
	copyBoundedStringMeta(meta, payload, "team_name", lifecycleMetaMaxChars, "team_name", "teamName")
	copyBoundedStringMeta(meta, payload, "tool_use_id", lifecycleMetaMaxChars, "tool_use_id", "toolUseID", "toolUseId")
	copyBoundedStringMeta(meta, payload, "status", lifecycleMetaMaxChars, "status")
	copyBoundedStringMeta(meta, payload, "transcript_path", lifecycleMetaMaxChars, "transcript_path", "transcriptPath")
	copyBoundedStringMeta(meta, payload, "agent_transcript_path", lifecycleMetaMaxChars, "agent_transcript_path", "agentTranscriptPath")
	if usage := lifecycleTokenUsage(firstAny(payload, "token_usage", "tokenUsage", "usage")); len(usage) > 0 {
		meta["token_usage"] = usage
	}
	return content, meta, runID, infer, parentReport, true
}

func stringFromAny(v any) string {
	switch x := v.(type) {
	case string:
		return x
	case fmt.Stringer:
		return x.String()
	case float64:
		if x == float64(int64(x)) {
			return strconv.FormatInt(int64(x), 10)
		}
		return strconv.FormatFloat(x, 'f', -1, 64)
	case bool:
		return strconv.FormatBool(x)
	default:
		return ""
	}
}

func stringField(m map[string]any, key string) string {
	if m == nil {
		return ""
	}
	return strings.TrimSpace(stringFromAny(m[key]))
}

func firstString(m map[string]any, keys ...string) string {
	for _, key := range keys {
		if s := stringField(m, key); s != "" {
			return s
		}
	}
	return ""
}

func firstAny(m map[string]any, keys ...string) any {
	for _, key := range keys {
		if v, ok := m[key]; ok {
			return v
		}
	}
	return nil
}

func int64Field(m map[string]any, key string) int64 {
	if m == nil {
		return 0
	}
	n, _ := int64FromAny(m[key])
	return n
}

func intField(m map[string]any, key string, def int) int {
	if m == nil {
		return def
	}
	n, ok := int64FromAny(m[key])
	if !ok {
		return def
	}
	return int(n)
}

func int64FromAny(v any) (int64, bool) {
	switch x := v.(type) {
	case float64:
		return int64(x), true
	case int:
		return int64(x), true
	case int64:
		return x, true
	case json.Number:
		n, err := x.Int64()
		if err == nil {
			return n, true
		}
		f, err := strconv.ParseFloat(x.String(), 64)
		if err != nil {
			return 0, false
		}
		return int64(f), true
	case string:
		n, err := strconv.ParseInt(strings.TrimSpace(x), 10, 64)
		if err != nil {
			return 0, false
		}
		return n, true
	default:
		return 0, false
	}
}

func nestedMap(v any) map[string]any {
	if m, ok := v.(map[string]any); ok {
		return m
	}
	return nil
}

func toolNameFromPayload(payload map[string]any) string {
	if name := firstString(payload, "tool_name", "toolName", "name"); name != "" {
		return name
	}
	for _, key := range []string{"tool", "tool_use", "toolUse"} {
		if m := nestedMap(payload[key]); m != nil {
			if name := firstString(m, "name", "tool_name", "toolName"); name != "" {
				return name
			}
		}
	}
	return ""
}

func valueShape(v any) string {
	switch x := v.(type) {
	case nil:
		return "null"
	case string:
		return "string"
	case float64:
		return "number"
	case bool:
		return "bool"
	case []any:
		return fmt.Sprintf("array[%d]", len(x))
	case map[string]any:
		keys := make([]string, 0, len(x))
		for key := range x {
			keys = append(keys, key)
		}
		sort.Strings(keys)
		if len(keys) > 12 {
			keys = append(keys[:12], "...")
		}
		return "object{" + strings.Join(keys, ",") + "}"
	default:
		return fmt.Sprintf("%T", v)
	}
}

func truncateText(s string, max int) string {
	scrubbed, ok := scrubTextForHandoff(s)
	if !ok {
		return ""
	}
	s = strings.TrimSpace(scrubbed)
	if max > 0 && len(s) > max {
		return s[:max] + "\n..."
	}
	return s
}

func textFromAny(v any) string {
	switch x := v.(type) {
	case nil:
		return ""
	case string:
		return x
	case []any:
		return flattenContent(x)
	case map[string]any:
		if content, ok := x["content"]; ok {
			return flattenContent(content)
		}
		for _, key := range []string{"text", "summary", "result", "message", "last_assistant_message", "prompt"} {
			if s := stringField(x, key); s != "" {
				return s
			}
		}
		b, err := json.Marshal(x)
		if err != nil {
			return fmt.Sprint(x)
		}
		return string(b)
	default:
		return stringFromAny(v)
	}
}

func textField(m map[string]any, key string) string {
	if m == nil {
		return ""
	}
	return strings.TrimSpace(textFromAny(m[key]))
}

func summarizeToolInput(toolName string, input any, maxChars int) string {
	m := nestedMap(input)
	switch toolName {
	case "Write", "Edit", "MultiEdit":
		paths := extractPaths(input, 10)
		parts := []string{"operation=" + toolName}
		if len(paths) > 0 {
			parts = append(parts, "paths="+strings.Join(paths, ", "))
		}
		if edits, ok := m["edits"].([]any); ok {
			parts = append(parts, fmt.Sprintf("edit_count=%d", len(edits)))
		}
		if content := stringField(m, "content"); content != "" {
			parts = append(parts, fmt.Sprintf("content_chars=%d", len(content)))
		}
		return strings.Join(parts, "; ")
	case "Bash":
		cmd := commandFromToolInput(input)
		parts := []string{"command_class=" + classifyCommand(cmd)}
		if cmd != "" {
			parts = append(parts, "command="+truncateText(cmd, maxChars))
		}
		return strings.Join(parts, "; ")
	case "Agent":
		parts := []string{}
		for _, key := range []string{"agent_type", "agentType", "description", "task", "prompt"} {
			if s := stringField(m, key); s != "" {
				parts = append(parts, key+"="+truncateText(s, maxChars/2))
			}
		}
		if len(parts) == 0 {
			return "shape=" + valueShape(input)
		}
		return strings.Join(parts, "; ")
	case "ExitPlanMode":
		for _, key := range []string{"plan", "content", "text"} {
			if s := stringField(m, key); s != "" {
				return "plan=" + truncateText(s, maxChars)
			}
		}
	}
	return "shape=" + valueShape(input)
}

func summarizeToolResponse(toolName string, response any, maxChars int) string {
	m := nestedMap(response)
	if m == nil {
		if s := stringFromAny(response); s != "" {
			return truncateText(s, maxChars)
		}
		return "shape=" + valueShape(response)
	}
	parts := []string{"shape=" + valueShape(response)}
	for _, key := range []string{"status", "exit_code", "exitCode"} {
		if s := stringField(m, key); s != "" {
			parts = append(parts, key+"="+s)
		}
	}
	switch toolName {
	case "Bash":
		for _, key := range []string{"stdout", "stderr", "output"} {
			if s := stringField(m, key); s != "" {
				parts = append(parts, key+"="+truncateText(s, maxChars/2))
			}
		}
	case "Agent":
		for _, key := range []string{"agentId", "agent_id", "summary", "result", "last_assistant_message", "message", "content"} {
			if s := textField(m, key); s != "" {
				parts = append(parts, key+"="+truncateText(s, maxChars/2))
			}
		}
	default:
		for _, key := range []string{"summary", "message"} {
			if s := stringField(m, key); s != "" {
				parts = append(parts, key+"="+truncateText(s, maxChars/2))
			}
		}
	}
	return strings.Join(parts, "; ")
}

func commandFromToolInput(input any) string {
	m := nestedMap(input)
	if m == nil {
		return ""
	}
	return firstString(m, "command", "cmd")
}

func classifyCommand(cmd string) string {
	fields := strings.Fields(strings.TrimSpace(cmd))
	if len(fields) == 0 {
		return ""
	}
	first := filepath.Base(fields[0])
	switch first {
	case "go", "pytest", "python", "python3", "npm", "pnpm", "yarn", "make":
		for _, f := range fields[1:] {
			if strings.Contains(f, "test") || strings.Contains(f, "pytest") {
				return "test"
			}
			if strings.Contains(f, "build") {
				return "build"
			}
		}
		if first == "pytest" {
			return "test"
		}
		return "build"
	case "git":
		return "git"
	default:
		return "other"
	}
}

func extractPaths(v any, limit int) []string {
	seen := map[string]bool{}
	var out []string
	var walk func(any)
	walk = func(cur any) {
		if len(out) >= limit {
			return
		}
		switch x := cur.(type) {
		case map[string]any:
			for key, val := range x {
				lower := strings.ToLower(key)
				if lower == "path" || lower == "file_path" || lower == "filepath" || lower == "notebook_path" {
					if s := strings.TrimSpace(stringFromAny(val)); s != "" && !seen[s] {
						seen[s] = true
						scrubbed, ok := scrubTextForHandoff(s)
						if ok {
							out = append(out, scrubbed)
						}
						if len(out) >= limit {
							return
						}
					}
				}
				walk(val)
			}
		case []any:
			for _, el := range x {
				walk(el)
			}
		}
	}
	walk(v)
	return out
}

func numericField(v any, keys ...string) (any, bool) {
	m := nestedMap(v)
	if m == nil {
		return nil, false
	}
	for _, key := range keys {
		switch x := m[key].(type) {
		case float64:
			if x == float64(int64(x)) {
				return int64(x), true
			}
			return x, true
		case int:
			return x, true
		case int64:
			return x, true
		case json.Number:
			return x.String(), true
		}
	}
	return nil, false
}

func eventKind(eventName string) string {
	var b strings.Builder
	for i, r := range eventName {
		if i > 0 && r >= 'A' && r <= 'Z' {
			b.WriteByte('-')
		}
		b.WriteByte(byte(strings.ToLower(string(r))[0]))
	}
	return b.String()
}

func lifecycleContent(eventName string, sid string, cwd string, payload map[string]any) string {
	parts := []string{
		fmt.Sprintf("Claude Code lifecycle event (event=%s, session_id=%s, cwd=%s)", eventName, sid, cwd),
	}
	for _, key := range []string{"agent_id", "agentId", "subagent_id", "subagentId", "agent_type", "agentType", "task_id", "taskId", "task_subject", "taskSubject", "task_description", "taskDescription", "teammate_name", "teammateName", "team_name", "teamName", "status", "description", "subject"} {
		if s := stringField(payload, key); s != "" {
			parts = append(parts, key+": "+truncateText(s, 2000))
		}
	}
	return strings.Join(parts, "\n")
}

func copyStringMeta(meta map[string]any, payload map[string]any, metaKey string, sourceKeys ...string) {
	if value := firstString(payload, sourceKeys...); value != "" {
		meta[metaKey] = value
	}
}

func copyBoundedStringMeta(meta map[string]any, payload map[string]any, metaKey string, maxChars int, sourceKeys ...string) {
	if value := firstString(payload, sourceKeys...); value != "" {
		value = truncateText(value, maxChars)
		if value != "" {
			meta[metaKey] = value
		}
	}
}

func lifecycleTokenUsage(v any) map[string]any {
	m := nestedMap(v)
	if m == nil {
		return nil
	}
	out := map[string]any{}
	for _, key := range []string{
		"input_tokens",
		"output_tokens",
		"cache_creation_input_tokens",
		"cache_read_input_tokens",
		"total_tokens",
	} {
		if value, ok := numericField(m, key); ok {
			out[key] = value
		}
	}
	if len(out) == 0 {
		return nil
	}
	return out
}

func maxFileChars() int {
	s := strings.TrimSpace(os.Getenv("POWERMEM_HOOK_MAX_CHARS"))
	if s == "" {
		return 8000
	}
	n, err := strconv.Atoi(s)
	if err != nil || n < 500 {
		return 8000
	}
	return n
}

func workerFile() {
	p := os.Getenv("POWERMEM_WORKER_FILE_PATH")
	if p == "" {
		return
	}
	data, err := os.ReadFile(p)
	if err != nil {
		os.Exit(1)
	}
	maxC := maxFileChars()
	if len(data) > maxC {
		data = append(data[:maxC], []byte("\n…")...)
	}
	if strings.TrimSpace(string(data)) == "" {
		return
	}
	abs, _ := filepath.Abs(p)
	if err := postMemory(string(data), map[string]any{
		"source": "powermem-file-watcher",
		"kind":   "workspace-file",
		"file":   abs,
	}, nil, inferFile()); err != nil {
		os.Exit(1)
	}
}

func flattenContent(v any) string {
	if v == nil {
		return ""
	}
	switch x := v.(type) {
	case string:
		return x
	case []any:
		var parts []string
		for _, el := range x {
			m, ok := el.(map[string]any)
			if !ok {
				if s, ok := el.(string); ok {
					parts = append(parts, s)
				}
				continue
			}
			if m["type"] == "text" {
				if t, ok := m["text"].(string); ok {
					parts = append(parts, t)
				}
			} else if t, ok := m["text"].(string); ok {
				parts = append(parts, t)
			} else {
				b, _ := json.Marshal(m)
				s := string(b)
				if len(s) > 2000 {
					s = s[:2000]
				}
				parts = append(parts, s)
			}
		}
		return strings.Join(parts, "\n")
	default:
		s := fmt.Sprint(x)
		if len(s) > 8000 {
			return s[:8000]
		}
		return s
	}
}

func flattenMessage(msg any) string {
	if msg == nil {
		return ""
	}
	s, ok := msg.(string)
	if ok {
		return s
	}
	m, ok := msg.(map[string]any)
	if !ok {
		out := fmt.Sprint(msg)
		if len(out) > 2000 {
			return out[:2000]
		}
		return out
	}
	if c, ok := m["content"]; ok {
		return flattenContent(c)
	}
	var bits []string
	for _, k := range []string{"text", "prompt"} {
		if t, ok := m[k].(string); ok {
			bits = append(bits, t)
		}
	}
	return strings.Join(bits, "\n")
}

func transcriptLineToText(obj map[string]any) string {
	t, _ := obj["type"].(string)
	switch t {
	case "summary":
		if s, ok := obj["summary"].(string); ok {
			return "[session title] " + s
		}
	case "user", "assistant":
		role := "User"
		if t == "assistant" {
			role = "Assistant"
		}
		body := strings.TrimSpace(flattenMessage(obj["message"]))
		if body != "" {
			return fmt.Sprintf("[%s]\n%s", role, body)
		}
		return "[" + role + "]"
	}
	return ""
}

func readTranscriptText(path string, maxChars int) (string, error) {
	f, err := os.Open(path)
	if err != nil {
		return "", err
	}
	defer f.Close()
	sc := bufio.NewScanner(f)
	buf := make([]byte, 0, 64*1024)
	sc.Buffer(buf, 1024*1024)
	var lines []string
	total := 0
	for sc.Scan() {
		line := strings.TrimSpace(sc.Text())
		if line == "" {
			continue
		}
		var obj map[string]any
		if json.Unmarshal([]byte(line), &obj) != nil || obj == nil {
			continue
		}
		chunk := transcriptLineToText(obj)
		if chunk == "" {
			continue
		}
		if total+len(chunk)+1 > maxChars {
			lines = append(lines, "… [truncated]")
			break
		}
		lines = append(lines, chunk)
		total += len(chunk) + 1
	}
	return strings.Join(lines, "\n\n---\n\n"), sc.Err()
}

func runWorkerFileSync(path string) error {
	self, err := os.Executable()
	if err != nil {
		return err
	}
	cmd := exec.Command(self, "worker-file")
	cmd.Env = append(os.Environ(), "POWERMEM_WORKER_FILE_PATH="+path)
	cmd.Stdin, cmd.Stdout, cmd.Stderr = nil, nil, nil
	return cmd.Run()
}
