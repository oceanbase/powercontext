package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"
)

// fallbackBaseURL reads POWERMEM_FALLBACK_BASE_URL. Returns empty when
// fallback is disabled or unset. Trailing slashes trimmed.
func fallbackBaseURL() string {
	if fallbackDisabled() {
		return ""
	}
	s := strings.TrimSpace(os.Getenv("POWERMEM_FALLBACK_BASE_URL"))
	if s == "" {
		return ""
	}
	return strings.TrimRight(s, "/")
}

func fallbackDisabled() bool {
	switch strings.ToLower(strings.TrimSpace(os.Getenv("POWERMEM_FALLBACK_DISABLED"))) {
	case "1", "true", "yes", "on":
		return true
	default:
		return false
	}
}

func fallbackAPIKey() string {
	return strings.TrimSpace(os.Getenv("POWERMEM_FALLBACK_API_KEY"))
}

func primaryAPIKey() string {
	return strings.TrimSpace(os.Getenv("POWERMEM_API_KEY"))
}

func fallbackDownTTL() time.Duration {
	return time.Duration(clampIntEnv("POWERMEM_FALLBACK_DOWN_TTL_SECONDS", 30, 5, 300)) * time.Second
}

func fallbackUpTTL() time.Duration {
	return time.Duration(clampIntEnv("POWERMEM_FALLBACK_UP_TTL_SECONDS", 30, 5, 300)) * time.Second
}

func triggerOn5xx() bool {
	return envBool("POWERMEM_FALLBACK_TRIGGER_5XX", true)
}

func replayEnabled() bool {
	return envBool("POWERMEM_FALLBACK_REPLAY", false)
}

func replayBatchSize() int {
	return clampIntEnv("POWERMEM_FALLBACK_REPLAY_BATCH", 50, 1, 500)
}

func replayInterval() time.Duration {
	return time.Duration(clampIntEnv("POWERMEM_FALLBACK_REPLAY_INTERVAL_SECONDS", 60, 5, 3600)) * time.Second
}

// clampIntEnv reads env var name; on parse failure/out-of-range returns def.
// Result is clamped to [min, max].
func clampIntEnv(name string, def, min, max int) int {
	s := strings.TrimSpace(os.Getenv(name))
	if s == "" {
		return def
	}
	n, err := strconv.Atoi(s)
	if err != nil {
		return def
	}
	if n < min {
		n = min
	}
	if n > max {
		n = max
	}
	return n
}

// envBool lives in main.go (added by the tool/lifecycle capture feature);
// we reuse it rather than re-declaring here.

// fallbackState is the persisted circuit-breaker state. Hook is a fresh
// process per event, so in-process mutexes don't survive between events.
type fallbackState struct {
	PrimaryDown bool      `json:"primary_down"`
	LastProbeAt time.Time `json:"last_probe_at"`
}

// shouldSkipProbe decides whether the caller may skip the primary probe.
// When skip=true, useFallback indicates which backend to use directly.
// When skip=false, the caller must probe primary and update state.
func (s fallbackState) shouldSkipProbe(now time.Time) (skip, useFallback bool) {
	if s.LastProbeAt.IsZero() {
		return false, false
	}
	if s.PrimaryDown {
		return now.Before(s.LastProbeAt.Add(fallbackDownTTL())), true
	}
	return now.Before(s.LastProbeAt.Add(fallbackUpTTL())), false
}

// stateFilePath returns $POWERMEM_DATA_DIR/fallback-state.json, defaulting
// to ~/.powermem/fallback-state.json.
func stateFilePath() string {
	d := strings.TrimSpace(os.Getenv("POWERMEM_DATA_DIR"))
	if d == "" {
		if h, err := os.UserHomeDir(); err == nil {
			d = filepath.Join(h, ".powermem")
		} else {
			d = "."
		}
	}
	return filepath.Join(d, "fallback-state.json")
}

var fallbackStateMu sync.Mutex

func loadFallbackState() fallbackState {
	fallbackStateMu.Lock()
	defer fallbackStateMu.Unlock()
	b, err := os.ReadFile(stateFilePath())
	if err != nil {
		return fallbackState{}
	}
	var s fallbackState
	if json.Unmarshal(b, &s) != nil {
		return fallbackState{}
	}
	return s
}

func saveFallbackState(s fallbackState) error {
	fallbackStateMu.Lock()
	defer fallbackStateMu.Unlock()
	p := stateFilePath()
	if err := os.MkdirAll(filepath.Dir(p), 0o700); err != nil {
		return err
	}
	b, err := json.Marshal(s)
	if err != nil {
		return err
	}
	tmp := p + ".tmp"
	if err := os.WriteFile(tmp, b, 0o600); err != nil {
		return err
	}
	return os.Rename(tmp, p)
}

// isFallbackTrigger returns true for network/timeout errors that indicate
// the primary is unreachable. 4xx and 2xx are not triggers.
func isFallbackTrigger(err error) bool {
	if err == nil {
		return false
	}
	if errors.Is(err, context.DeadlineExceeded) {
		return true
	}
	var netErr net.Error
	if errors.As(err, &netErr) {
		return true
	}
	return false
}

// isFallbackTriggerStatus returns true for HTTP 5xx when TRIGGER_5XX is on.
func isFallbackTriggerStatus(code int) bool {
	return code >= 500 && code < 600 && triggerOn5xx()
}

// doRequestOnce sends method to base+path with body and content-type.
// apiKey (if non-empty) is set as X-API-Key. timeout bounds the whole call.
func doRequestOnce(base, apiKey, method, path string, body []byte, contentType string, timeout time.Duration) (*http.Response, error) {
	req, err := http.NewRequest(method, base+path, bytes.NewReader(body))
	if err != nil {
		return nil, err
	}
	if contentType != "" {
		req.Header.Set("Content-Type", contentType)
	}
	if apiKey != "" {
		req.Header.Set("X-API-Key", apiKey)
	}
	c := &http.Client{Timeout: timeout}
	return c.Do(req)
}

// doRequestWithFallback routes a request to primary, falling back when
// primary is unreachable (network/timeout/5xx). State is persisted to
// fallback-state.json so subsequent hook invocations skip probes within TTL.
//
// Returns (resp, err). err is non-nil when the final response (primary or
// fallback) is missing or non-2xx. Callers should nil-check resp.
//
// Single-backend fast path: when POWERMEM_FALLBACK_BASE_URL is empty, this
// behaves as a direct doRequestOnce against primary, with non-2xx surfaced
// as an error.
func doRequestWithFallback(method, path string, body []byte, contentType string, timeout time.Duration) (*http.Response, error) {
	primary := baseURL()
	fallback := fallbackBaseURL()

	// Fast path: no fallback configured.
	if fallback == "" {
		resp, err := doRequestOnce(primary, primaryAPIKey(), method, path, body, contentType, timeout)
		return finalizeResponse(resp, err)
	}

	st := loadFallbackState()
	now := time.Now()
	skip, useFB := st.shouldSkipProbe(now)
	if skip && useFB {
		// Cached down within TTL → go straight to fallback.
		resp, err := doRequestOnce(fallback, fallbackAPIKey(), method, path, body, contentType, timeout)
		return finalizeResponse(resp, err)
	}

	// Cached up or expired → try primary.
	resp, err := doRequestOnce(primary, primaryAPIKey(), method, path, body, contentType, timeout)
	primaryOK := err == nil && resp != nil && resp.StatusCode >= 200 && resp.StatusCode < 300
	if primaryOK {
		st.PrimaryDown = false
		st.LastProbeAt = now
		_ = saveFallbackState(st)
		return resp, nil
	}

	// Primary failed. Decide whether to fall back.
	trigger := false
	if err != nil && isFallbackTrigger(err) {
		trigger = true
	} else if err == nil && resp != nil && isFallbackTriggerStatus(resp.StatusCode) {
		trigger = true
	}

	if !trigger {
		// Non-trigger failure (e.g. 4xx) — surface as-is, do not mark down.
		if resp != nil {
			_ = resp.Body.Close()
		}
		return finalizeResponse(resp, err)
	}

	// Fall back.
	st.PrimaryDown = true
	st.LastProbeAt = now
	_ = saveFallbackState(st)
	logFallback(primary, err, resp)
	if resp != nil {
		_ = resp.Body.Close()
	}
	fbResp, fbErr := doRequestOnce(fallback, fallbackAPIKey(), method, path, body, contentType, timeout)
	return finalizeResponse(fbResp, fbErr)
}

// finalizeResponse closes the body and returns an error on non-2xx. If err
// is already non-nil, it is returned unchanged. If resp is nil, returns
// (nil, err).
func finalizeResponse(resp *http.Response, err error) (*http.Response, error) {
	if err != nil || resp == nil {
		return resp, err
	}
	if resp.StatusCode >= 200 && resp.StatusCode < 300 {
		return resp, nil
	}
	code := resp.StatusCode
	_ = resp.Body.Close()
	return resp, fmtErr("http", code)
}

// logFallback writes a JSON line to POWERMEM_FALLBACK_LOG_FILE (default
// ~/.powermem/powermem-hook.log). Failures are silent — logging is best-effort.
func logFallback(primary string, err error, resp *http.Response) {
	p := strings.TrimSpace(os.Getenv("POWERMEM_FALLBACK_LOG_FILE"))
	if p == "" {
		if d := strings.TrimSpace(os.Getenv("POWERMEM_DATA_DIR")); d != "" {
			p = filepath.Join(d, "powermem-hook.log")
		} else if h, e := os.UserHomeDir(); e == nil {
			p = filepath.Join(h, ".powermem", "powermem-hook.log")
		} else {
			return
		}
	}
	_ = os.MkdirAll(filepath.Dir(p), 0o700)
	entry := map[string]any{
		"ts":      time.Now().UTC().Format(time.RFC3339),
		"event":   "primary_failed",
		"primary": primary,
	}
	if err != nil {
		entry["reason"] = err.Error()
	} else if resp != nil {
		entry["status"] = resp.StatusCode
	}
	b, e := json.Marshal(entry)
	if e != nil {
		return
	}
	f, e := os.OpenFile(p, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o600)
	if e != nil {
		return
	}
	defer f.Close()
	_, _ = f.Write(append(b, '\n'))
}

// drainBody fully reads and discards resp.Body so the connection can be
// reused. Returns the bytes for callers that need to parse the response.
func drainBody(resp *http.Response) ([]byte, error) {
	if resp == nil || resp.Body == nil {
		return nil, nil
	}
	defer resp.Body.Close()
	return io.ReadAll(resp.Body)
}

// fmtErr formats an HTTP error with status code for surfacing.
func fmtErr(prefix string, code int) error {
	return fmt.Errorf("%s %d", prefix, code)
}
