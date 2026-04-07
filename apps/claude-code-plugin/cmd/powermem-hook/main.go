// powermem-hook: Claude Code hook — stdin JSON (SessionEnd / PostCompact) → background HTTP POST to PowerMem.
// Cross-platform; zero runtime deps beyond the single binary.
package main

import (
	"bufio"
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"time"
)

// Default REST base when POWERMEM_BASE_URL is unset (matches .mcp.json local server).
const defaultPowerMemBaseURL = "http://localhost:8000"

func main() {
	if len(os.Args) >= 2 {
		switch os.Args[1] {
		case "worker-transcript":
			workerTranscript()
			return
		case "worker-compact":
			workerCompact()
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

func spawnWorker(mode string, envExtra map[string]string) {
	self, err := os.Executable()
	if err != nil {
		return
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
	_ = cmd.Start()
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
		spawnWorker("worker-transcript", map[string]string{
			"POWERMEM_WORKER_TRANSCRIPT_PATH": tp,
			"POWERMEM_WORKER_SESSION_ID":      sid,
			"POWERMEM_WORKER_CWD":             cwd,
			"POWERMEM_WORKER_REASON":          reason,
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
		spawnWorker("worker-compact", map[string]string{
			"POWERMEM_WORKER_COMPACT_SUMMARY": summary,
			"POWERMEM_WORKER_SESSION_ID":      sid,
			"POWERMEM_WORKER_CWD":             cwd,
			"POWERMEM_WORKER_TRIGGER":         trigger,
		})
	}
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

func inferFile() bool {
	switch strings.ToLower(strings.TrimSpace(os.Getenv("POWERMEM_INFER_FILE"))) {
	case "1", "true", "yes":
		return true
	default:
		return false
	}
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
	if u := strings.TrimSpace(os.Getenv("POWERMEM_USER_ID")); u != "" {
		return u
	}
	if u := os.Getenv("USER"); u != "" {
		return u
	}
	return os.Getenv("USERNAME")
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

func handleUserPromptSubmit(payload map[string]any) {
	if !promptSearchEnabled() {
		return
	}
	prompt, _ := payload["prompt"].(string)
	prompt = strings.TrimSpace(prompt)
	if len(prompt) < 2 {
		return
	}
	ctx, err := searchMemoriesForPrompt(prompt)
	if err != nil || strings.TrimSpace(ctx) == "" {
		return
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
	base := baseURL()
	body := map[string]any{
		"query": query,
		"limit": promptSearchLimit(),
	}
	if u := searchBodyUserID(); u != "" {
		body["user_id"] = u
	}
	if a := searchBodyAgentID(); a != "" {
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
	b.WriteString("## PowerMem (retrieved for this prompt)\n\nRelevant long-term memories from PowerMem; use if they help answer the user. Ignore if unrelated.\n\n")
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

func postMemory(content string, meta map[string]any, runID *string, infer bool) error {
	base := baseURL()
	body := map[string]any{
		"content":  content,
		"infer":    infer,
		"metadata": meta,
	}
	if u := strings.TrimSpace(os.Getenv("POWERMEM_USER_ID")); u != "" {
		body["user_id"] = u
	} else if u := os.Getenv("USER"); u != "" {
		body["user_id"] = u
	} else if u := os.Getenv("USERNAME"); u != "" {
		body["user_id"] = u
	}
	if a := strings.TrimSpace(os.Getenv("POWERMEM_AGENT_ID")); a != "" {
		body["agent_id"] = a
	}
	if runID != nil && *runID != "" {
		body["run_id"] = *runID
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
	if path == "" {
		return
	}
	text, err := readTranscriptText(path, maxHookChars())
	if err != nil || strings.TrimSpace(text) == "" {
		return
	}
	sid := os.Getenv("POWERMEM_WORKER_SESSION_ID")
	cwd := os.Getenv("POWERMEM_WORKER_CWD")
	reason := os.Getenv("POWERMEM_WORKER_REASON")
	header := fmt.Sprintf("Claude Code session transcript (session_id=%s, cwd=%s, reason=%s)\n\n", sid, cwd, reason)
	runID := sid
	if err := postMemory(header+text, map[string]any{
		"source":             "claude-code-hook",
		"kind":               "session-end-transcript",
		"transcript_path":    path,
		"session_id":         sid,
		"cwd":                cwd,
		"session_end_reason": reason,
	}, &runID, inferTranscript()); err != nil {
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
	if err := postMemory(content, map[string]any{
		"source":          "claude-code-hook",
		"kind":            "post-compact-summary",
		"session_id":      sid,
		"cwd":             cwd,
		"compact_trigger": trigger,
	}, &runID, inferCompact()); err != nil {
		os.Exit(1)
	}
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
