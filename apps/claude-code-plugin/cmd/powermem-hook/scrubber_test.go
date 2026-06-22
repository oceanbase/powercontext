package main

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func syntheticToken() string {
	return "Ab1_" + strings.Repeat("Cd2-", 12)
}

func syntheticJWT() string {
	return strings.Join([]string{
		"eyJ" + strings.Repeat("a", 16),
		strings.Repeat("B", 20),
		strings.Repeat("c", 20),
	}, ".")
}

func privateKeyFixture() string {
	phrase := "PRIVATE" + " KEY"
	return strings.Join([]string{
		"-----BEGIN " + phrase + "-----",
		"not-real-key-material",
		"-----END " + phrase + "-----",
	}, "\n")
}

func TestScrubTextDefaultCoverage(t *testing.T) {
	cfg := defaultHookPrivacyConfig()
	raw := syntheticToken()
	bareRaw := "Bare" + strings.Repeat("Aa7_", 12)
	lowerRaw := "short-lower-value"
	tokenRaw := "short-token-value"
	jwt := syntheticJWT()
	longToken := "Long" + strings.Repeat("Aa9_", 12)
	slackToken := "xoxb-" + strings.Repeat("12ab-", 10) + "9Z"
	googleToken := "ya29." + strings.Repeat("Ab3_", 8)
	githubPAT := "github_pat_" + strings.Repeat("Cd4_", 8)
	input := strings.Join([]string{
		"SERVICE_" + "TOKEN" + "=" + raw,
		"API_" + "KEY=" + bareRaw,
		"api_" + "key=" + lowerRaw,
		`{"api_` + `key":"` + raw + `"}`,
		`{"to` + `ken":"` + tokenRaw + `"}`,
		"to" + "ken: " + tokenRaw,
		"pass" + "word: " + raw,
		"Authorization: Bearer " + raw,
		"https://user:" + raw + "@example.com/path",
		"https://" + bareRaw + "@example.com/path",
		"https://example.com/callback?access_" + "token=" + raw,
		privateKeyFixture(),
		jwt,
		longToken,
		slackToken,
		googleToken,
		githubPAT,
		"normal public sentence",
	}, "\n")

	out, report := scrubText(input, cfg)
	for _, value := range []string{raw, bareRaw, lowerRaw, tokenRaw, jwt, longToken, slackToken, googleToken, githubPAT, "not-real-key-material"} {
		if strings.Contains(out, value) {
			t.Fatalf("scrubbed output retained raw value %q in %q", value, out)
		}
	}
	if report.SecretRedactions < 15 {
		t.Fatalf("expected broad high-confidence redaction coverage, got %+v", report)
	}
	if !strings.Contains(out, "normal public sentence") {
		t.Fatalf("non-sensitive text was lost: %q", out)
	}
}

func TestScrubTextFalsePositiveGuardrails(t *testing.T) {
	cfg := defaultHookPrivacyConfig()
	hash := strings.Repeat("abcdef0123456789", 4)
	input := "The password policy requires 12 characters; sha256 " + hash + " is an identifier."

	out, report := scrubText(input, cfg)
	if out != input {
		t.Fatalf("expected guardrail text to remain unchanged:\nwant %q\ngot  %q", input, out)
	}
	if report.SecretRedactions != 0 {
		t.Fatalf("expected no high-confidence redactions, got %+v", report)
	}
}

func TestStrictPIIRedaction(t *testing.T) {
	cfg := defaultHookPrivacyConfig()
	cfg.PrivacyLevel = "strict"
	email := "dev" + "@example.com"
	phone := "+1 415 555 0101"

	out, report := scrubText("Contact "+email+" or "+phone, cfg)
	if strings.Contains(out, email) || strings.Contains(out, phone) {
		t.Fatalf("strict output retained PII: %q", out)
	}
	if report.PIIRedactions != 2 {
		t.Fatalf("expected two PII redactions, got %+v", report)
	}
}

func TestPathPrivacyModes(t *testing.T) {
	t.Setenv("HOME", "/example-home/alice")
	t.Setenv("USERPROFILE", `C:\Example\Alice`)

	cfg := defaultHookPrivacyConfig()
	out, report := scrubText("open /example-home/alice/project/file.go", cfg)
	if !strings.Contains(out, "~/project/file.go") {
		t.Fatalf("home path was not reduced: %q", out)
	}
	if report.PathRedactions != 1 {
		t.Fatalf("expected one path redaction, got %+v", report)
	}
	value, report := scrubMetadataString("cwd", `C:\Example\Alice\project`, cfg)
	if value != `~\project` || report.PathRedactions != 1 {
		t.Fatalf("windows home path privacy failed: value=%q report=%+v", value, report)
	}

	cfg.PathPrivacy = "basename"
	value, report = scrubMetadataString("transcript_path", "/workspace/alice/project/transcript.jsonl", cfg)
	if value != "transcript.jsonl" || report.PathRedactions != 1 {
		t.Fatalf("basename path privacy failed: value=%q report=%+v", value, report)
	}
	if display := scrubPathForDisplay("/workspace/alice/project/file.go", cfg); display != "file.go" {
		t.Fatalf("display path privacy failed: %q", display)
	}
	out, report = scrubText("open /workspace/alice/project/secret-plan.md", cfg)
	if strings.Contains(out, "/workspace/alice") || !strings.Contains(out, "secret-plan.md") {
		t.Fatalf("basename text path privacy failed: out=%q report=%+v", out, report)
	}
	if report.PathRedactions != 1 {
		t.Fatalf("expected one basename text path redaction, got %+v", report)
	}
	displayURL := scrubTextForDisplay("https://"+syntheticToken()+"@example.com/api?to"+"ken="+syntheticToken(), cfg)
	if strings.Contains(displayURL, "Cd2") {
		t.Fatalf("display URL retained sentinel fragments: %q", displayURL)
	}

	cfg.PathPrivacy = "omit"
	value, report = scrubMetadataString("file", "/workspace/alice/project/note.md", cfg)
	if value != omittedPath || report.PathRedactions != 1 {
		t.Fatalf("omit path privacy failed: value=%q report=%+v", value, report)
	}
}

func TestDefaultPathPrivacyHandlesNonHomePaths(t *testing.T) {
	t.Setenv("HOME", "/example-home/alice")
	cfg := defaultHookPrivacyConfig()

	value, report := scrubMetadataString("cwd", "/opt/private/project", cfg)
	if value != "project" || report.PathRedactions != 1 {
		t.Fatalf("default non-home metadata path privacy failed: value=%q report=%+v", value, report)
	}

	out, report := scrubText("cwd=/repo/project opened /opt/private/file.go", cfg)
	for _, leaked := range []string{"/repo/project", "/opt/private"} {
		if strings.Contains(out, leaked) {
			t.Fatalf("default non-home content path leaked %q in %q", leaked, out)
		}
	}
	if !strings.Contains(out, "cwd=project") || !strings.Contains(out, "file.go") {
		t.Fatalf("default non-home content path basename was not preserved: %q", out)
	}
	if report.PathRedactions != 2 {
		t.Fatalf("expected two default non-home path redactions, got %+v", report)
	}
}

func TestPathPrivacyDoesNotRewriteOrdinaryURLPaths(t *testing.T) {
	cfg := defaultHookPrivacyConfig()
	t.Setenv("HOME", "/example-home/alice")

	homeURL := "https://docs.example.com/example-home/alice/file.html"
	out, report := scrubText("read "+homeURL, cfg)
	if !strings.Contains(out, homeURL) {
		t.Fatalf("ordinary URL containing home path was rewritten: %q", out)
	}
	if report.PathRedactions != 0 || report.SecretRedactions != 0 {
		t.Fatalf("ordinary URL containing home path should remain untouched, got %+v", report)
	}

	prefixNeighbor := "/example-home/alice2/project/file.go"
	out, report = scrubText("open "+prefixNeighbor, cfg)
	if !strings.Contains(out, "file.go") || strings.Contains(out, "~2") {
		t.Fatalf("home-prefix neighbor was corrupted: %q", out)
	}

	cfg.PathPrivacy = "basename"
	url := "https://docs.example.com/a/file.html"

	out, report = scrubText("read "+url, cfg)
	if !strings.Contains(out, url) {
		t.Fatalf("ordinary URL path was rewritten: %q", out)
	}
	if report.PathRedactions != 0 || report.SecretRedactions != 0 {
		t.Fatalf("ordinary URL should not count as path or secret redaction, got %+v", report)
	}

	cfg.PathPrivacy = "omit"
	out, report = scrubText("read "+url, cfg)
	if !strings.Contains(out, url) {
		t.Fatalf("ordinary URL path was omitted: %q", out)
	}
	if report.PathRedactions != 0 || report.SecretRedactions != 0 {
		t.Fatalf("ordinary URL should remain untouched in omit mode, got %+v", report)
	}

	route := "POST /api/v1/memories/search"
	out, report = scrubText(route, defaultHookPrivacyConfig())
	if out != route || report.PathRedactions != 0 {
		t.Fatalf("root-relative API route was rewritten: out=%q report=%+v", out, report)
	}

	route = "GET /api/v1/openapi.json and /static/app.js"
	out, report = scrubText(route, defaultHookPrivacyConfig())
	if out != route || report.PathRedactions != 0 {
		t.Fatalf("root-relative route with extension was rewritten: out=%q report=%+v", out, report)
	}

	route = "path=/api/v1/openapi.json STATIC_PATH=/static/app.js"
	out, report = scrubText(route, defaultHookPrivacyConfig())
	if out != route || report.PathRedactions != 0 {
		t.Fatalf("keyed root-relative route was rewritten: out=%q report=%+v", out, report)
	}

	filePath := "file=/src/customer/private/repo/config.yaml path=/api/v1/openapi.json"
	cfg.PathPrivacy = "basename"
	out, report = scrubText(filePath, cfg)
	if strings.Contains(out, "/src/customer") || !strings.Contains(out, "file=config.yaml") || !strings.Contains(out, "path=/api/v1/openapi.json") {
		t.Fatalf("keyed file path/root route handling failed: out=%q report=%+v", out, report)
	}

	barePath := "opened /src/customer/private/repo/config.yaml and routed /api/v1/openapi.json"
	out, report = scrubText(barePath, cfg)
	if strings.Contains(out, "/src/customer") || !strings.Contains(out, "config.yaml") || !strings.Contains(out, "/api/v1/openapi.json") {
		t.Fatalf("bare generic file path/root route handling failed: out=%q report=%+v", out, report)
	}
}

func TestPromptSearchPolicy(t *testing.T) {
	raw := syntheticToken()
	prompt := "please use Authorization: Bearer " + raw

	cfg := defaultHookPrivacyConfig()
	query, ok := scrubPromptForSearch(prompt, cfg)
	if ok || query != "" {
		t.Fatalf("default policy should skip high-confidence query, got ok=%v query=%q", ok, query)
	}

	cfg.SearchSecretPolicy = "redact"
	query, ok = scrubPromptForSearch(prompt, cfg)
	if !ok || strings.Contains(query, raw) || !strings.Contains(query, redactedSecret) {
		t.Fatalf("redact policy failed, ok=%v query=%q", ok, query)
	}

	cfg = defaultHookPrivacyConfig()
	query, ok = scrubPromptForSearch("please use Bearer abcdefghijk carefully", cfg)
	if !ok || strings.Contains(query, "abcdefghijk") || !strings.Contains(query, redactedSecret) {
		t.Fatalf("loose bearer token should redact without skipping, ok=%v query=%q", ok, query)
	}

	cfg = defaultHookPrivacyConfig()
	googleToken := "ya29." + strings.Repeat("Ab3_", 8)
	query, ok = scrubPromptForSearch("please use "+googleToken, cfg)
	if ok || query != "" {
		t.Fatalf("default policy should skip Google OAuth token, ok=%v query=%q", ok, query)
	}

	cfg.SearchSecretPolicy = "off"
	cfg.PathPrivacy = "basename"
	query, ok = scrubPromptForSearch(prompt+" file=/src/customer/private/repo/config.yaml", cfg)
	if !ok || !strings.Contains(query, raw) || strings.Contains(query, "/src/customer") || !strings.Contains(query, "file=config.yaml") {
		t.Fatalf("off policy should keep secret policy off while still scrubbing paths, ok=%v query=%q", ok, query)
	}

	query, ok = scrubPromptForSearch("look up https://example.com/items?key=sort", defaultHookPrivacyConfig())
	if !ok || !strings.Contains(query, "key=sort") {
		t.Fatalf("ordinary key query parameter should not skip search, ok=%v query=%q", ok, query)
	}
}

func TestHandleUserPromptSubmitDoesNotSearchWhenDisabledOrHighConfidenceSecret(t *testing.T) {
	requests := 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requests++
		t.Fatalf("unexpected prompt search request: %s", r.URL.Path)
	}))
	defer server.Close()
	t.Setenv("POWERMEM_BASE_URL", server.URL)

	handleUserPromptSubmit(map[string]any{
		"prompt": "please use Authorization: Bearer " + syntheticToken(),
	})
	if requests != 0 {
		t.Fatalf("high-confidence secret prompt should not trigger search, got %d request(s)", requests)
	}

	t.Setenv("POWERMEM_PROMPT_SEARCH", "0")
	handleUserPromptSubmit(map[string]any{
		"prompt": "ordinary prompt",
	})
	if requests != 0 {
		t.Fatalf("disabled prompt search should not trigger search, got %d request(s)", requests)
	}
}

func TestCompactScrubBeforeWorkerEnv(t *testing.T) {
	t.Setenv("HOME", "/workspace/alice")
	raw := syntheticToken()
	cfg := defaultHookPrivacyConfig()

	summary, cwd, report, ok := scrubCompactForWorker("Authorization: Bearer "+raw, "/workspace/alice/project", cfg)
	if !ok {
		t.Fatalf("redact mode should keep compact worker enabled")
	}
	if strings.Contains(summary, raw) || strings.Contains(cwd, "/workspace/alice") {
		t.Fatalf("compact env values were not scrubbed: summary=%q cwd=%q", summary, cwd)
	}
	if report.SecretRedactions != 1 || report.PathRedactions != 1 {
		t.Fatalf("unexpected compact report: %+v", report)
	}

	cfg.SecretAction = "block"
	_, _, report, ok = scrubCompactForWorker("Authorization: Bearer "+raw, "/workspace/alice/project", cfg)
	if ok || report.SecretRedactions != 1 {
		t.Fatalf("block mode should skip compact worker, ok=%v report=%+v", ok, report)
	}
}

func TestSessionEndScrubsCWDForWorkerEnv(t *testing.T) {
	cfg := defaultHookPrivacyConfig()

	cwd, report, ok := scrubCWDForWorker("/workspace/alice/private-project", cfg)
	if !ok {
		t.Fatalf("redact mode should keep SessionEnd worker enabled")
	}
	if cwd != "private-project" || strings.Contains(cwd, "/workspace/alice") {
		t.Fatalf("SessionEnd worker cwd was not scrubbed: cwd=%q report=%+v", cwd, report)
	}
	if report.PathRedactions != 1 {
		t.Fatalf("expected one SessionEnd cwd path redaction, got %+v", report)
	}
}

func TestPostMemoryRedactsHTTPPayload(t *testing.T) {
	t.Setenv("HOME", "/workspace/alice")
	t.Setenv("POWERMEM_HOOK_PATH_PRIVACY", "basename")
	raw := syntheticToken()
	var requestBody string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, err := io.ReadAll(r.Body)
		if err != nil {
			t.Fatalf("failed to read request body: %v", err)
		}
		requestBody = string(body)
		w.WriteHeader(http.StatusOK)
	}))
	defer server.Close()
	t.Setenv("POWERMEM_BASE_URL", server.URL)

	err := postMemory("Authorization: Bearer "+raw+" in /workspace/alice/project/note.md", map[string]any{
		"source":          "test",
		"kind":            "workspace-file",
		"file":            "/workspace/alice/project/note.md",
		"transcript_path": "/workspace/alice/project/transcript.jsonl",
	}, nil, false)
	if err != nil {
		t.Fatalf("postMemory returned error: %v", err)
	}
	if requestBody == "" {
		t.Fatal("expected fake server to receive a request")
	}
	for _, value := range []string{raw, "/workspace/alice/project/note.md", "/workspace/alice/project/transcript.jsonl"} {
		if strings.Contains(requestBody, value) {
			t.Fatalf("request body retained raw value %q in %s", value, requestBody)
		}
	}
	if !strings.Contains(requestBody, "note.md") || !strings.Contains(requestBody, "transcript.jsonl") {
		t.Fatalf("basename metadata was not preserved: %s", requestBody)
	}

	var decoded map[string]any
	if err := json.Unmarshal([]byte(requestBody), &decoded); err != nil {
		t.Fatalf("request body was not JSON: %v", err)
	}
	meta, ok := decoded["metadata"].(map[string]any)
	if !ok {
		t.Fatalf("metadata missing from request body: %#v", decoded)
	}
	privacy, ok := meta["privacy"].(map[string]any)
	if !ok {
		t.Fatalf("privacy metadata missing: %#v", meta)
	}
	if privacy["scrubbed"] != true || privacy["level"] != "standard" || privacy["path_privacy"] != "basename" {
		t.Fatalf("unexpected privacy metadata: %#v", privacy)
	}
}

func TestPostMemoryRedactsTopLevelIdentifiers(t *testing.T) {
	rawUser := "User" + strings.Repeat("Aa7_", 12)
	rawAgent := "Agent" + strings.Repeat("Bb8_", 12)
	rawRun := "Run" + strings.Repeat("Cc9_", 12)
	var requestBody string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, err := io.ReadAll(r.Body)
		if err != nil {
			t.Fatalf("failed to read request body: %v", err)
		}
		requestBody = string(body)
		w.WriteHeader(http.StatusOK)
	}))
	defer server.Close()
	t.Setenv("POWERMEM_BASE_URL", server.URL)
	t.Setenv("POWERMEM_USER_ID", "API_"+"KEY="+rawUser)
	t.Setenv("POWERMEM_AGENT_ID", "https://"+rawAgent+"@example.com")
	runID := "TO" + "KEN=" + rawRun

	if err := postMemory("clean content", map[string]any{"kind": "test"}, &runID, false); err != nil {
		t.Fatalf("postMemory returned error: %v", err)
	}
	for _, value := range []string{rawUser, rawAgent, rawRun} {
		if strings.Contains(requestBody, value) {
			t.Fatalf("request body retained top-level value %q in %s", value, requestBody)
		}
	}
}

func TestSearchBodyRedactsIdentifiers(t *testing.T) {
	rawUser := "User" + strings.Repeat("Ee5_", 12)
	rawAgent := "Agent" + strings.Repeat("Ff6_", 12)
	var requestBody string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, err := io.ReadAll(r.Body)
		if err != nil {
			t.Fatalf("failed to read request body: %v", err)
		}
		requestBody = string(body)
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"data":{"results":[{"content":"clean memory","score":0.7}]}}`))
	}))
	defer server.Close()
	t.Setenv("POWERMEM_BASE_URL", server.URL)
	t.Setenv("POWERMEM_HOOK_SEARCH_SECRET_POLICY", "redact")
	t.Setenv("POWERMEM_USER_ID", "API_"+"KEY="+rawUser)
	t.Setenv("POWERMEM_AGENT_ID", "https://"+rawAgent+"@example.com")

	ctx, err := searchMemoriesForPrompt("clean query")
	if err != nil {
		t.Fatalf("searchMemoriesForPrompt returned error: %v", err)
	}
	if !strings.Contains(ctx, "clean memory") {
		t.Fatalf("unexpected search context: %q", ctx)
	}
	for _, value := range []string{rawUser, rawAgent} {
		if strings.Contains(requestBody, value) {
			t.Fatalf("search request body retained raw value %q in %s", value, requestBody)
		}
	}
}

func TestSearchBodyScrubsIdentifiersWithSecretPolicyOff(t *testing.T) {
	var requestBody string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, err := io.ReadAll(r.Body)
		if err != nil {
			t.Fatalf("failed to read request body: %v", err)
		}
		requestBody = string(body)
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"data":{"results":[{"content":"clean memory","score":0.7}]}}`))
	}))
	defer server.Close()
	t.Setenv("POWERMEM_BASE_URL", server.URL)
	t.Setenv("POWERMEM_HOOK_SEARCH_SECRET_POLICY", "off")
	t.Setenv("POWERMEM_HOOK_PRIVACY_LEVEL", "strict")
	t.Setenv("POWERMEM_HOOK_PATH_PRIVACY", "basename")
	t.Setenv("POWERMEM_USER_ID", "/src/customer/private/repo/alice@example.com")
	t.Setenv("POWERMEM_AGENT_ID", "agent@example.com")

	ctx, err := searchMemoriesForPrompt("clean query")
	if err != nil {
		t.Fatalf("searchMemoriesForPrompt returned error: %v", err)
	}
	if !strings.Contains(ctx, "clean memory") {
		t.Fatalf("unexpected search context: %q", ctx)
	}
	for _, leaked := range []string{"/src/customer", "alice@example.com", "agent@example.com"} {
		if strings.Contains(requestBody, leaked) {
			t.Fatalf("search request body retained raw identifier value %q in %s", leaked, requestBody)
		}
	}
	var body map[string]any
	if err := json.Unmarshal([]byte(requestBody), &body); err != nil {
		t.Fatalf("search request body was not valid JSON: %v", err)
	}
	if body["user_id"] != redactedPII || body["agent_id"] != redactedPII {
		t.Fatalf("search request body did not retain strict PII redaction markers: %v", body)
	}
}

func TestParentScrubReportNotDoubleCounted(t *testing.T) {
	t.Setenv("HOME", "/workspace/alice")
	t.Setenv("POWERMEM_HOOK_PATH_PRIVACY", "home")
	raw := "Compact" + strings.Repeat("Dd4_", 12)
	cfg := defaultHookPrivacyConfig()
	summary, cwd, parentReport, ok := scrubCompactForWorker("API_"+"KEY="+raw, "/workspace/alice/project", cfg)
	if !ok {
		t.Fatal("expected compact pre-scrub to continue in redact mode")
	}
	if parentReport.SecretRedactions != 1 || parentReport.PathRedactions != 1 {
		t.Fatalf("unexpected parent report: %+v", parentReport)
	}

	var requestBody string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, err := io.ReadAll(r.Body)
		if err != nil {
			t.Fatalf("failed to read request body: %v", err)
		}
		requestBody = string(body)
		w.WriteHeader(http.StatusOK)
	}))
	defer server.Close()
	t.Setenv("POWERMEM_BASE_URL", server.URL)

	if err := postMemoryWithScrubReport("summary\n"+summary, map[string]any{"cwd": cwd}, nil, false, parentReport); err != nil {
		t.Fatalf("postMemoryWithScrubReport returned error: %v", err)
	}
	var decoded map[string]any
	if err := json.Unmarshal([]byte(requestBody), &decoded); err != nil {
		t.Fatalf("request body was not JSON: %v", err)
	}
	meta := decoded["metadata"].(map[string]any)
	privacy := meta["privacy"].(map[string]any)
	redactions := privacy["redactions"].(map[string]any)
	if redactions["secrets"].(float64) != 1 || redactions["paths"].(float64) != 1 {
		t.Fatalf("parent scrub counts were not preserved accurately: %#v", redactions)
	}
}

func TestPathPrivacyDoesNotHideKeyedSecrets(t *testing.T) {
	t.Setenv("POWERMEM_HOOK_PATH_PRIVACY", "basename")
	raw := "Path" + strings.Repeat("Gg7_", 12)
	var requestBody string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, err := io.ReadAll(r.Body)
		if err != nil {
			t.Fatalf("failed to read request body: %v", err)
		}
		requestBody = string(body)
		w.WriteHeader(http.StatusOK)
	}))
	defer server.Close()
	t.Setenv("POWERMEM_BASE_URL", server.URL)

	path := "/workspace/project/file.txt?to" + "ken=" + raw
	if err := postMemory("opened "+path, map[string]any{"file": path}, nil, false); err != nil {
		t.Fatalf("postMemory returned error: %v", err)
	}
	if strings.Contains(requestBody, raw) {
		t.Fatalf("path-shaped keyed value was not redacted: %s", requestBody)
	}
	var decoded map[string]any
	if err := json.Unmarshal([]byte(requestBody), &decoded); err != nil {
		t.Fatalf("request body was not JSON: %v", err)
	}
	content, _ := decoded["content"].(string)
	meta := decoded["metadata"].(map[string]any)
	file, _ := meta["file"].(string)
	want := "file.txt?to" + "ken=" + redactedSecret
	if !strings.Contains(content, want) || file != want {
		t.Fatalf("redacted basename path was not preserved: content=%q file=%q", content, file)
	}
	privacy := meta["privacy"].(map[string]any)
	redactions := privacy["redactions"].(map[string]any)
	if redactions["secrets"].(float64) != 2 {
		t.Fatalf("path-shaped secret should be counted once per surface, got %#v", redactions)
	}
}

func TestContentPathPrivacyHandlesSpaces(t *testing.T) {
	t.Setenv("POWERMEM_HOOK_PATH_PRIVACY", "basename")
	var requestBody string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, err := io.ReadAll(r.Body)
		if err != nil {
			t.Fatalf("failed to read request body: %v", err)
		}
		requestBody = string(body)
		w.WriteHeader(http.StatusOK)
	}))
	defer server.Close()
	t.Setenv("POWERMEM_BASE_URL", server.URL)

	path := "/workspace/My Project/file.txt"
	if err := postMemory("opened "+path, map[string]any{"kind": "test"}, nil, false); err != nil {
		t.Fatalf("postMemory returned error: %v", err)
	}
	if strings.Contains(requestBody, "My Project") || strings.Contains(requestBody, "/workspace") {
		t.Fatalf("content path with spaces was not reduced: %s", requestBody)
	}
	if !strings.Contains(requestBody, "file.txt") {
		t.Fatalf("basename was not preserved for content path with spaces: %s", requestBody)
	}
}

func TestContentPathPrivacyHandlesAdjacentSpacedPaths(t *testing.T) {
	cfg := defaultHookPrivacyConfig()
	cfg.PathPrivacy = "basename"
	input := "opened /workspace/My Project/file.go and /workspace/Other Project/other.go"

	out, report := scrubText(input, cfg)
	if strings.Contains(out, "/workspace/") || !strings.Contains(out, "file.go and other.go") {
		t.Fatalf("adjacent spaced paths were not handled independently: out=%q report=%+v", out, report)
	}
	if report.PathRedactions != 2 || report.SecretRedactions != 0 {
		t.Fatalf("unexpected adjacent spaced path report: %+v", report)
	}
}

func TestContentPathPrivacyHandlesGenericUnixPaths(t *testing.T) {
	t.Setenv("POWERMEM_HOOK_PATH_PRIVACY", "basename")
	var requestBody string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, err := io.ReadAll(r.Body)
		if err != nil {
			t.Fatalf("failed to read request body: %v", err)
		}
		requestBody = string(body)
		w.WriteHeader(http.StatusOK)
	}))
	defer server.Close()
	t.Setenv("POWERMEM_BASE_URL", server.URL)

	content := strings.Join([]string{
		"opened /repo/project/file.txt",
		"PWD=/path/to/project/notes.md",
		"then cd PWD=/path/to/project",
		"plus /Volumes/Example/PrivateRepo",
		"/nix/store/private-project",
		"/usr/local/private-project",
		"/etc/ssl/private",
		"/media/alice/project",
		"/Applications/Secret App.",
		"/src/customer/private/repo/config.yaml",
		"/src/customer/private/worktree",
		"cwd=/workspace/Alice Secret Project/repo",
		"/" + "home/alice/My Project/repo",
		"/tmp/Alice Temp/repo",
	}, " ")
	if err := postMemory(content, map[string]any{"kind": "test"}, nil, false); err != nil {
		t.Fatalf("postMemory returned error: %v", err)
	}
	for _, leaked := range []string{"/repo/project", "/path/to/project", "/Volumes/Example", "/nix/store", "/usr/local", "/etc/ssl", "/media/alice", "/Applications/", "/src/customer", "/workspace/Alice", "Secret Project/repo", "/" + "home/alice", "My Project/repo", "/tmp/Alice", "Alice Temp/repo"} {
		if strings.Contains(requestBody, leaked) {
			t.Fatalf("generic content path leaked %q in %s", leaked, requestBody)
		}
	}
	if !strings.Contains(requestBody, "file.txt") || !strings.Contains(requestBody, "notes.md") || !strings.Contains(requestBody, "PWD=project") ||
		!strings.Contains(requestBody, "PrivateRepo") || !strings.Contains(requestBody, "private-project") ||
		!strings.Contains(requestBody, "private") || !strings.Contains(requestBody, "project") || !strings.Contains(requestBody, "Secret App") ||
		!strings.Contains(requestBody, "config.yaml") || !strings.Contains(requestBody, "worktree") ||
		!strings.Contains(requestBody, "cwd=repo") || !strings.Contains(requestBody, "repo") {
		t.Fatalf("generic content path basename was not preserved: %s", requestBody)
	}
}

func TestContentPathPrivacyHandlesFileURLsAndWindowsSlashPaths(t *testing.T) {
	t.Setenv("POWERMEM_HOOK_PATH_PRIVACY", "basename")
	var requestBody string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, err := io.ReadAll(r.Body)
		if err != nil {
			t.Fatalf("failed to read request body: %v", err)
		}
		requestBody = string(body)
		w.WriteHeader(http.StatusOK)
	}))
	defer server.Close()
	t.Setenv("POWERMEM_BASE_URL", server.URL)

	content := `opened C:/Example/Alice/project/file.go and file:///workspace/alice/project/note.md and cwd=C:\Example\Alice\My Project\repo`
	if err := postMemory(content, map[string]any{"kind": "test"}, nil, false); err != nil {
		t.Fatalf("postMemory returned error: %v", err)
	}
	for _, leaked := range []string{"C:/Example/Alice/project", "file:///workspace/alice/project", `C:\Example\Alice\My Project`, `My Project\repo`} {
		if strings.Contains(requestBody, leaked) {
			t.Fatalf("filesystem path leaked %q in %s", leaked, requestBody)
		}
	}
	if !strings.Contains(requestBody, "file.go") || !strings.Contains(requestBody, "note.md") || !strings.Contains(requestBody, "cwd=repo") {
		t.Fatalf("filesystem path basenames were not preserved: %s", requestBody)
	}
}

func TestPathPrivacyHandlesUNCPaths(t *testing.T) {
	t.Setenv("POWERMEM_HOOK_PATH_PRIVACY", "basename")
	cfg := loadHookPrivacyConfig()
	unc := `\\fileserver\share\Alice\Private Project\repo`

	value, report := scrubMetadataString("cwd", unc, cfg)
	if value != "repo" || report.PathRedactions != 1 {
		t.Fatalf("metadata UNC path privacy failed: value=%q report=%+v", value, report)
	}
	if display := scrubPathForDisplay(unc, cfg); display != "repo" {
		t.Fatalf("display UNC path privacy failed: %q", display)
	}

	out, report := scrubText("cwd="+unc, cfg)
	if strings.Contains(out, `\\fileserver`) || strings.Contains(out, `Private Project`) {
		t.Fatalf("content UNC path leaked: %q", out)
	}
	if !strings.Contains(out, "cwd=repo") || report.PathRedactions != 1 {
		t.Fatalf("content UNC path basename/report failed: out=%q report=%+v", out, report)
	}

	out, report = scrubText(`open \\fileserver\private-share`, cfg)
	if strings.Contains(out, `\\fileserver`) || !strings.Contains(out, "private-share") || report.PathRedactions != 1 {
		t.Fatalf("bare UNC share root was not scrubbed: out=%q report=%+v", out, report)
	}
}

func TestPathPrivacyRedactsCredentialPrefixedBasename(t *testing.T) {
	t.Setenv("POWERMEM_HOOK_PATH_PRIVACY", "basename")
	raw := "s" + "k-" + strings.Repeat("a", 24)

	out, report := scrubText("opened /tmp/"+raw, loadHookPrivacyConfig())
	if strings.Contains(out, raw) {
		t.Fatalf("credential-prefixed path basename was retained: %q", out)
	}
	if !strings.Contains(out, redactedSecret) {
		t.Fatalf("credential-prefixed path basename was not redacted: %q", out)
	}
	if report.PathRedactions != 1 || report.SecretRedactions != 1 {
		t.Fatalf("unexpected credential-prefixed path report: %+v", report)
	}
}

func TestPathPrivacyRedactsLongTokenBasename(t *testing.T) {
	t.Setenv("POWERMEM_HOOK_SECRET_ACTION", "block")
	t.Setenv("POWERMEM_HOOK_PATH_PRIVACY", "basename")
	raw := syntheticToken()
	hits := 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		hits++
		w.WriteHeader(http.StatusOK)
	}))
	defer server.Close()
	t.Setenv("POWERMEM_BASE_URL", server.URL)

	if err := postMemory("opened /workspace/project/"+raw, map[string]any{"kind": "test"}, nil, false); err != nil {
		t.Fatalf("postMemory returned error: %v", err)
	}
	if hits != 0 {
		t.Fatalf("block mode should skip long token path basenames, got %d request(s)", hits)
	}
}

func TestHomePathPrivacyRedactsLongTokenBasename(t *testing.T) {
	t.Setenv("HOME", "/example-home/alice")
	raw := syntheticToken()
	cfg := defaultHookPrivacyConfig()

	query, ok := scrubPromptForSearch("opened /example-home/alice/project/"+raw, cfg)
	if ok || query != "" {
		t.Fatalf("default prompt search should skip home path token basename, ok=%v query=%q", ok, query)
	}
	query, ok = scrubPromptForSearch("opened /example-home/alice/My Project/"+raw, cfg)
	if ok || query != "" {
		t.Fatalf("default prompt search should skip spaced home path token basename, ok=%v query=%q", ok, query)
	}

	t.Setenv("POWERMEM_HOOK_SECRET_ACTION", "block")
	hits := 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		hits++
		w.WriteHeader(http.StatusOK)
	}))
	defer server.Close()
	t.Setenv("POWERMEM_BASE_URL", server.URL)

	if err := postMemory("opened /example-home/alice/project/"+raw, map[string]any{"kind": "test"}, nil, false); err != nil {
		t.Fatalf("postMemory returned error: %v", err)
	}
	if hits != 0 {
		t.Fatalf("block mode should skip home path token basenames, got %d request(s)", hits)
	}
}

func TestQuotedAndYAMLKeyedPathsOutsideKnownRoots(t *testing.T) {
	cfg := defaultHookPrivacyConfig()
	cfg.PathPrivacy = "basename"
	input := strings.Join([]string{
		`"file":"/src/customer/private/repo/config.yaml"`,
		`"cwd":"/src/customer/private/worktree"`,
		`'path'='/src/customer/private/repo/path.txt'`,
		`transcript_path: /src/customer/private/session.jsonl`,
		`cwd: "/src/customer/private/repo/cwd-dir" # keep comment`,
		`path: '/src/customer/private/repo/quoted.yaml'`,
	}, "\n")

	out, report := scrubText(input, cfg)
	for _, leaked := range []string{"/src/customer", "/private/repo", "/private/session"} {
		if strings.Contains(out, leaked) {
			t.Fatalf("quoted/YAML keyed path leaked %q in %q", leaked, out)
		}
	}
	for _, basename := range []string{"config.yaml", "worktree", "path.txt", "session.jsonl", "cwd-dir", "quoted.yaml"} {
		if !strings.Contains(out, basename) {
			t.Fatalf("quoted/YAML keyed path basename %q missing from %q", basename, out)
		}
	}
	if !strings.Contains(out, `cwd: "cwd-dir" # keep comment`) {
		t.Fatalf("YAML quoted cwd path did not preserve quote/comment around basename: %q", out)
	}
	if report.PathRedactions != 6 || report.SecretRedactions != 0 {
		t.Fatalf("unexpected quoted/YAML keyed path report: %+v", report)
	}

	query, ok := scrubPromptForSearch(input, cfg)
	if !ok || strings.Contains(query, "/src/customer") {
		t.Fatalf("quoted/YAML keyed path leaked through prompt search: ok=%v query=%q", ok, query)
	}
	content, meta, blocked := scrubMemoryPayload(input, map[string]any{"cwd": "/src/customer/private/repo/meta"}, cfg, scrubReport{})
	if blocked || strings.Contains(content, "/src/customer") || meta["cwd"] != "meta" {
		t.Fatalf("quoted/YAML keyed path leaked through memory payload: blocked=%v content=%q meta=%v", blocked, content, meta)
	}
}

func TestPWDPathFieldsAreNotTreatedAsSecrets(t *testing.T) {
	cfg := defaultHookPrivacyConfig()
	cfg.PathPrivacy = "basename"
	input := "{\"PWD\":\"/workspace/project\"}\nPWD: /workspace/other-project"

	out, report := scrubText(input, cfg)
	if strings.Contains(out, "/workspace/") || strings.Contains(out, redactedSecret) {
		t.Fatalf("PWD path fields were not handled as paths: out=%q report=%+v", out, report)
	}
	if !strings.Contains(out, `"PWD":"project"`) || !strings.Contains(out, "PWD: other-project") {
		t.Fatalf("PWD path field basenames were not preserved: out=%q report=%+v", out, report)
	}
	if report.SecretRedactions != 0 || report.PathRedactions != 2 {
		t.Fatalf("unexpected PWD path field report: %+v", report)
	}
	query, ok := scrubPromptForSearch(input, cfg)
	if !ok || strings.Contains(query, redactedSecret) {
		t.Fatalf("PWD path field should not skip prompt search, ok=%v query=%q", ok, query)
	}
}

func TestBlockModeDoesNotTreatOrdinaryPathAsSecret(t *testing.T) {
	t.Setenv("POWERMEM_HOOK_SECRET_ACTION", "block")
	t.Setenv("POWERMEM_HOOK_PATH_PRIVACY", "basename")
	hits := 0
	var requestBody string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		hits++
		body, err := io.ReadAll(r.Body)
		if err != nil {
			t.Fatalf("failed to read request body: %v", err)
		}
		requestBody = string(body)
		w.WriteHeader(http.StatusOK)
	}))
	defer server.Close()
	t.Setenv("POWERMEM_BASE_URL", server.URL)

	path := "/workspace/project/issue-1047/subdir/Release_Notes_20260616_Alpha_Beta_Gamma_Delta_Version_One.md"
	content := "opened " + path + "\nPWD=" + path
	_, contentReport := scrubText(content, loadHookPrivacyConfig())
	_, metaReport := scrubMetadata(map[string]any{"file": path}, loadHookPrivacyConfig())
	if contentReport.SecretRedactions != 0 || metaReport.SecretRedactions != 0 {
		t.Fatalf("ordinary path produced secret redactions: content=%+v metadata=%+v", contentReport, metaReport)
	}
	if err := postMemory(content, map[string]any{"file": path}, nil, false); err != nil {
		t.Fatalf("postMemory returned error: %v", err)
	}
	if hits != 1 {
		t.Fatalf("ordinary path should not be blocked, got %d request(s)", hits)
	}
	if strings.Contains(requestBody, "/workspace/project") || strings.Contains(requestBody, redactedSecret) {
		t.Fatalf("ordinary path was not handled through path privacy: %s", requestBody)
	}
	if !strings.Contains(requestBody, "Release_Notes_20260616_Alpha_Beta_Gamma_Delta_Version_One.md") {
		t.Fatalf("basename path was not preserved: %s", requestBody)
	}
}

func TestBlockModeDoesNotTreatLongDirectoryBasenameAsSecret(t *testing.T) {
	t.Setenv("POWERMEM_HOOK_SECRET_ACTION", "block")
	t.Setenv("POWERMEM_HOOK_PATH_PRIVACY", "basename")
	hits := 0
	var requestBody string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		hits++
		body, err := io.ReadAll(r.Body)
		if err != nil {
			t.Fatalf("failed to read request body: %v", err)
		}
		requestBody = string(body)
		w.WriteHeader(http.StatusOK)
	}))
	defer server.Close()
	t.Setenv("POWERMEM_BASE_URL", server.URL)

	path := "/workspace/project/Release_Notes_20260616_Alpha_Beta_Gamma_Delta_Version_One"
	content := "cwd " + path + "\nPWD=" + path
	if err := postMemory(content, map[string]any{"cwd": path}, nil, false); err != nil {
		t.Fatalf("postMemory returned error: %v", err)
	}
	if hits != 1 {
		t.Fatalf("ordinary directory basename should not be blocked, got %d request(s)", hits)
	}
	if strings.Contains(requestBody, "/workspace/project") || strings.Contains(requestBody, redactedSecret) {
		t.Fatalf("ordinary directory basename was not handled through path privacy: %s", requestBody)
	}
	if !strings.Contains(requestBody, "Release_Notes_20260616_Alpha_Beta_Gamma_Delta_Version_One") {
		t.Fatalf("basename path was not preserved: %s", requestBody)
	}
}

func TestBlockModeSkipsPathWithKeyedSecret(t *testing.T) {
	t.Setenv("POWERMEM_HOOK_SECRET_ACTION", "block")
	t.Setenv("POWERMEM_HOOK_PATH_PRIVACY", "basename")
	hits := 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		hits++
		w.WriteHeader(http.StatusOK)
	}))
	defer server.Close()
	t.Setenv("POWERMEM_BASE_URL", server.URL)

	path := "/workspace/project/file.txt?to" + "ken=" + syntheticToken()
	if err := postMemory("opened "+path, map[string]any{"file": path}, nil, false); err != nil {
		t.Fatalf("postMemory returned error: %v", err)
	}
	if hits != 0 {
		t.Fatalf("block mode should skip path-shaped keyed values, got %d request(s)", hits)
	}
}

func TestBlockModeSkipsOmittedPathWithKeyedSecret(t *testing.T) {
	t.Setenv("POWERMEM_HOOK_SECRET_ACTION", "block")
	t.Setenv("POWERMEM_HOOK_PATH_PRIVACY", "omit")
	hits := 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		hits++
		w.WriteHeader(http.StatusOK)
	}))
	defer server.Close()
	t.Setenv("POWERMEM_BASE_URL", server.URL)

	path := "/workspace/project/file.txt?to" + "ken=" + syntheticToken()
	if err := postMemory("opened "+path, map[string]any{"file": path}, nil, false); err != nil {
		t.Fatalf("postMemory returned error: %v", err)
	}
	if hits != 0 {
		t.Fatalf("block mode should skip omitted path-shaped keyed values, got %d request(s)", hits)
	}
}

func TestBlockModeSkipsPathWithSecretInDirectory(t *testing.T) {
	t.Setenv("POWERMEM_HOOK_SECRET_ACTION", "block")
	t.Setenv("POWERMEM_HOOK_PATH_PRIVACY", "basename")
	hits := 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		hits++
		w.WriteHeader(http.StatusOK)
	}))
	defer server.Close()
	t.Setenv("POWERMEM_BASE_URL", server.URL)

	path := "/workspace/project?to" + "ken=" + syntheticToken() + "/file.txt"
	if err := postMemory("opened "+path, map[string]any{"file": path}, nil, false); err != nil {
		t.Fatalf("postMemory returned error: %v", err)
	}
	if hits != 0 {
		t.Fatalf("block mode should skip paths with hidden directory secrets, got %d request(s)", hits)
	}
}

func TestBlockModeSkipsContentOnlyPathWithKeyedSecret(t *testing.T) {
	t.Setenv("POWERMEM_HOOK_SECRET_ACTION", "block")
	t.Setenv("POWERMEM_HOOK_PATH_PRIVACY", "basename")
	hits := 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		hits++
		w.WriteHeader(http.StatusOK)
	}))
	defer server.Close()
	t.Setenv("POWERMEM_BASE_URL", server.URL)

	content := "opened /workspace/project/file.txt?to" + "ken=" + syntheticToken()
	if err := postMemory(content, map[string]any{"kind": "test"}, nil, false); err != nil {
		t.Fatalf("postMemory returned error: %v", err)
	}
	if hits != 0 {
		t.Fatalf("block mode should skip content-only path-shaped keyed values, got %d request(s)", hits)
	}
}

func TestStrictPIIInContentPathBasename(t *testing.T) {
	t.Setenv("POWERMEM_HOOK_PRIVACY_LEVEL", "strict")
	t.Setenv("POWERMEM_HOOK_PATH_PRIVACY", "basename")
	email := "dev" + "@example.com"
	var requestBody string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, err := io.ReadAll(r.Body)
		if err != nil {
			t.Fatalf("failed to read request body: %v", err)
		}
		requestBody = string(body)
		w.WriteHeader(http.StatusOK)
	}))
	defer server.Close()
	t.Setenv("POWERMEM_BASE_URL", server.URL)

	if err := postMemory("opened /workspace/project/"+email, map[string]any{"kind": "test"}, nil, false); err != nil {
		t.Fatalf("postMemory returned error: %v", err)
	}
	if strings.Contains(requestBody, email) {
		t.Fatalf("strict mode retained PII in content path basename: %s", requestBody)
	}
	var decoded map[string]any
	if err := json.Unmarshal([]byte(requestBody), &decoded); err != nil {
		t.Fatalf("request body was not JSON: %v", err)
	}
	content, _ := decoded["content"].(string)
	if !strings.Contains(content, redactedPII) {
		t.Fatalf("strict mode did not record redacted PII in content path basename: %q", content)
	}
}

func TestPostMemoryBlockModeSkipsHTTPWrite(t *testing.T) {
	hits := 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		hits++
		w.WriteHeader(http.StatusOK)
	}))
	defer server.Close()
	t.Setenv("POWERMEM_BASE_URL", server.URL)
	t.Setenv("POWERMEM_HOOK_SECRET_ACTION", "block")

	err := postMemory("Authorization: Bearer "+syntheticToken(), map[string]any{"kind": "test"}, nil, false)
	if err != nil {
		t.Fatalf("block mode should exit successfully, got %v", err)
	}
	if hits != 0 {
		t.Fatalf("block mode should skip HTTP writes, got %d request(s)", hits)
	}
}
