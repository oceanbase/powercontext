package main

import (
	"bytes"
	"context"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func setFallbackEnv(t *testing.T, primary, fallback string) {
	t.Helper()
	t.Setenv("POWERMEM_BASE_URL", primary)
	t.Setenv("POWERMEM_FALLBACK_BASE_URL", fallback)
	t.Setenv("POWERMEM_DATA_DIR", t.TempDir())
}

func TestFallbackBaseURLDisabled(t *testing.T) {
	t.Setenv("POWERMEM_FALLBACK_BASE_URL", "http://localhost:8849")
	t.Setenv("POWERMEM_FALLBACK_DISABLED", "1")
	if got := fallbackBaseURL(); got != "" {
		t.Errorf("expected empty when disabled, got %q", got)
	}
}

func TestFallbackBaseURLTrimsSlash(t *testing.T) {
	t.Setenv("POWERMEM_FALLBACK_BASE_URL", "http://localhost:8849/")
	t.Setenv("POWERMEM_FALLBACK_DISABLED", "")
	if got := fallbackBaseURL(); got != "http://localhost:8849" {
		t.Errorf("expected trimmed URL, got %q", got)
	}
}

func TestIsFallbackTrigger(t *testing.T) {
	if isFallbackTrigger(nil) {
		t.Error("nil should not trigger")
	}
	if !isFallbackTrigger(context.DeadlineExceeded) {
		t.Error("DeadlineExceeded should trigger")
	}
	// net.OpError example
	_, err := netDialRefused()
	if !isFallbackTrigger(err) {
		t.Errorf("dial error should trigger, got %v", err)
	}
}

func TestIsFallbackTriggerStatus(t *testing.T) {
	t.Setenv("POWERMEM_FALLBACK_TRIGGER_5XX", "1")
	if !isFallbackTriggerStatus(503) {
		t.Error("503 should trigger when 5xx enabled")
	}
	if isFallbackTriggerStatus(404) {
		t.Error("404 should not trigger")
	}
	if isFallbackTriggerStatus(200) {
		t.Error("200 should not trigger")
	}
	t.Setenv("POWERMEM_FALLBACK_TRIGGER_5XX", "0")
	if isFallbackTriggerStatus(503) {
		t.Error("503 should not trigger when 5xx disabled")
	}
}

func TestShouldSkipProbeCachedDown(t *testing.T) {
	t.Setenv("POWERMEM_FALLBACK_DOWN_TTL_SECONDS", "30")
	st := fallbackState{PrimaryDown: true, LastProbeAt: time.Now()}
	skip, useFB := st.shouldSkipProbe(time.Now())
	if !skip || !useFB {
		t.Error("cached down within TTL should skip probe and use fallback")
	}
}

func TestShouldSkipProbeCachedDownExpired(t *testing.T) {
	t.Setenv("POWERMEM_FALLBACK_DOWN_TTL_SECONDS", "5")
	st := fallbackState{PrimaryDown: true, LastProbeAt: time.Now().Add(-1 * time.Hour)}
	skip, _ := st.shouldSkipProbe(time.Now())
	if skip {
		t.Error("expired down should require probe")
	}
}

func TestShouldSkipProbeCachedUp(t *testing.T) {
	t.Setenv("POWERMEM_FALLBACK_UP_TTL_SECONDS", "30")
	st := fallbackState{PrimaryDown: false, LastProbeAt: time.Now()}
	skip, useFB := st.shouldSkipProbe(time.Now())
	if !skip || useFB {
		t.Error("cached up within TTL should skip probe and use primary")
	}
}

func TestShouldSkipProbeZeroState(t *testing.T) {
	var st fallbackState
	skip, _ := st.shouldSkipProbe(time.Now())
	if skip {
		t.Error("zero state should require probe")
	}
}

func TestSaveLoadFallbackState(t *testing.T) {
	dir := t.TempDir()
	t.Setenv("POWERMEM_DATA_DIR", dir)
	st := fallbackState{PrimaryDown: true, LastProbeAt: time.Now().UTC()}
	if err := saveFallbackState(st); err != nil {
		t.Fatalf("save: %v", err)
	}
	loaded := loadFallbackState()
	if !loaded.PrimaryDown {
		t.Error("loaded should have PrimaryDown=true")
	}
	if loaded.LastProbeAt.IsZero() {
		t.Error("loaded should have LastProbeAt set")
	}
}

func TestLoadFallbackStateMissingFile(t *testing.T) {
	t.Setenv("POWERMEM_DATA_DIR", t.TempDir())
	st := loadFallbackState()
	if st.PrimaryDown || !st.LastProbeAt.IsZero() {
		t.Error("missing file should return zero state")
	}
}

func TestDoRequestWithFallbackSingleBackend(t *testing.T) {
	// No fallback URL → single-backend fast path, no state file written.
	dir := t.TempDir()
	t.Setenv("POWERMEM_DATA_DIR", dir)
	t.Setenv("POWERMEM_FALLBACK_BASE_URL", "")
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte("ok"))
	}))
	defer srv.Close()
	t.Setenv("POWERMEM_BASE_URL", srv.URL)
	resp, err := doRequestWithFallback(http.MethodPost, "/api/v1/memories", []byte("{}"), "application/json", 5*time.Second)
	if err != nil {
		t.Fatalf("err: %v", err)
	}
	_, _ = drainBody(resp)
	// state file should not exist
	if _, err := os.Stat(filepath.Join(dir, "fallback-state.json")); !os.IsNotExist(err) {
		t.Errorf("state file should not exist in single-backend mode: %v", err)
	}
}

func TestDoRequestWithFallbackPrimaryOK(t *testing.T) {
	dir := t.TempDir()
	t.Setenv("POWERMEM_DATA_DIR", dir)
	t.Setenv("POWERMEM_FALLBACK_DOWN_TTL_SECONDS", "30")
	t.Setenv("POWERMEM_FALLBACK_UP_TTL_SECONDS", "30")

	var primaryHit, fallbackHit int
	primary := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		primaryHit++
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte("primary"))
	}))
	defer primary.Close()
	fallback := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		fallbackHit++
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte("fallback"))
	}))
	defer fallback.Close()
	t.Setenv("POWERMEM_BASE_URL", primary.URL)
	t.Setenv("POWERMEM_FALLBACK_BASE_URL", fallback.URL)

	resp, err := doRequestWithFallback(http.MethodPost, "/api/v1/memories", []byte("{}"), "application/json", 5*time.Second)
	if err != nil {
		t.Fatalf("err: %v", err)
	}
	b, _ := drainBody(resp)
	if !bytes.Contains(b, []byte("primary")) {
		t.Errorf("expected primary response, got %s", b)
	}
	if primaryHit != 1 || fallbackHit != 0 {
		t.Errorf("hits: primary=%d fallback=%d", primaryHit, fallbackHit)
	}
	st := loadFallbackState()
	if st.PrimaryDown {
		t.Error("state should be up after primary success")
	}
}

func TestDoRequestWithFallbackPrimaryDown(t *testing.T) {
	dir := t.TempDir()
	t.Setenv("POWERMEM_DATA_DIR", dir)
	t.Setenv("POWERMEM_FALLBACK_DOWN_TTL_SECONDS", "30")

	var fallbackHit int
	// primary: close listener to force connection refused
	primary := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {}))
	primaryURL := primary.URL
	primary.Close()
	fallback := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		fallbackHit++
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte("fallback"))
	}))
	defer fallback.Close()
	t.Setenv("POWERMEM_BASE_URL", primaryURL)
	t.Setenv("POWERMEM_FALLBACK_BASE_URL", fallback.URL)

	resp, err := doRequestWithFallback(http.MethodPost, "/api/v1/memories", []byte("{}"), "application/json", 5*time.Second)
	if err != nil {
		t.Fatalf("err: %v", err)
	}
	b, _ := drainBody(resp)
	if !bytes.Contains(b, []byte("fallback")) {
		t.Errorf("expected fallback response, got %s", b)
	}
	if fallbackHit != 1 {
		t.Errorf("expected 1 fallback hit, got %d", fallbackHit)
	}
	st := loadFallbackState()
	if !st.PrimaryDown {
		t.Error("state should be down after primary failure")
	}
}

func TestDoRequestWithFallback5xx(t *testing.T) {
	dir := t.TempDir()
	t.Setenv("POWERMEM_DATA_DIR", dir)
	t.Setenv("POWERMEM_FALLBACK_TRIGGER_5XX", "1")
	t.Setenv("POWERMEM_FALLBACK_DOWN_TTL_SECONDS", "30")

	var fallbackHit int
	primary := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusServiceUnavailable)
	}))
	defer primary.Close()
	fallback := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		fallbackHit++
		w.WriteHeader(http.StatusOK)
	}))
	defer fallback.Close()
	t.Setenv("POWERMEM_BASE_URL", primary.URL)
	t.Setenv("POWERMEM_FALLBACK_BASE_URL", fallback.URL)

	resp, err := doRequestWithFallback(http.MethodPost, "/api/v1/memories", []byte("{}"), "application/json", 5*time.Second)
	if err != nil {
		t.Fatalf("err: %v", err)
	}
	_, _ = drainBody(resp)
	if fallbackHit != 1 {
		t.Errorf("expected 1 fallback hit on 5xx, got %d", fallbackHit)
	}
}

func TestDoRequestWithFallback5xxDisabled(t *testing.T) {
	dir := t.TempDir()
	t.Setenv("POWERMEM_DATA_DIR", dir)
	t.Setenv("POWERMEM_FALLBACK_TRIGGER_5XX", "0")

	var fallbackHit int
	primary := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusServiceUnavailable)
	}))
	defer primary.Close()
	fallback := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		fallbackHit++
		w.WriteHeader(http.StatusOK)
	}))
	defer fallback.Close()
	t.Setenv("POWERMEM_BASE_URL", primary.URL)
	t.Setenv("POWERMEM_FALLBACK_BASE_URL", fallback.URL)

	resp, err := doRequestWithFallback(http.MethodPost, "/api/v1/memories", []byte("{}"), "application/json", 5*time.Second)
	if err == nil {
		_, _ = drainBody(resp)
		t.Error("expected error on 5xx when TRIGGER_5XX=0")
	}
	if fallbackHit != 0 {
		t.Errorf("fallback should not be hit when TRIGGER_5XX=0, got %d", fallbackHit)
	}
}

func TestDoRequestWithFallbackCachedDownSkipsPrimary(t *testing.T) {
	dir := t.TempDir()
	t.Setenv("POWERMEM_DATA_DIR", dir)
	t.Setenv("POWERMEM_FALLBACK_DOWN_TTL_SECONDS", "60")

	var primaryHit, fallbackHit int
	primary := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		primaryHit++
		w.WriteHeader(http.StatusOK)
	}))
	defer primary.Close()
	fallback := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		fallbackHit++
		w.WriteHeader(http.StatusOK)
	}))
	defer fallback.Close()
	t.Setenv("POWERMEM_BASE_URL", primary.URL)
	t.Setenv("POWERMEM_FALLBACK_BASE_URL", fallback.URL)

	// Seed state as down within TTL.
	st := fallbackState{PrimaryDown: true, LastProbeAt: time.Now()}
	if err := saveFallbackState(st); err != nil {
		t.Fatal(err)
	}

	resp, err := doRequestWithFallback(http.MethodPost, "/api/v1/memories", []byte("{}"), "application/json", 5*time.Second)
	if err != nil {
		t.Fatalf("err: %v", err)
	}
	_, _ = drainBody(resp)
	if primaryHit != 0 || fallbackHit != 1 {
		t.Errorf("cached down should skip primary, got primary=%d fallback=%d", primaryHit, fallbackHit)
	}
}

func TestDoRequestWithFallbackDisabledKillSwitch(t *testing.T) {
	dir := t.TempDir()
	t.Setenv("POWERMEM_DATA_DIR", dir)
	t.Setenv("POWERMEM_FALLBACK_DISABLED", "1")

	var fallbackHit int
	primary := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))
	defer primary.Close()
	fallback := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		fallbackHit++
		w.WriteHeader(http.StatusOK)
	}))
	defer fallback.Close()
	t.Setenv("POWERMEM_BASE_URL", primary.URL)
	t.Setenv("POWERMEM_FALLBACK_BASE_URL", fallback.URL)

	resp, err := doRequestWithFallback(http.MethodPost, "/api/v1/memories", []byte("{}"), "application/json", 5*time.Second)
	if err != nil {
		t.Fatalf("err: %v", err)
	}
	_, _ = drainBody(resp)
	if fallbackHit != 0 {
		t.Errorf("fallback should not be hit when disabled, got %d", fallbackHit)
	}
	if _, err := os.Stat(filepath.Join(dir, "fallback-state.json")); !os.IsNotExist(err) {
		t.Errorf("state file should not exist when disabled: %v", err)
	}
}

func TestLogFallbackWritesJSONLine(t *testing.T) {
	dir := t.TempDir()
	t.Setenv("POWERMEM_DATA_DIR", dir)
	logPath := filepath.Join(dir, "hook.log")
	t.Setenv("POWERMEM_FALLBACK_LOG_FILE", logPath)
	logFallback("http://primary:8848", context.DeadlineExceeded, nil)
	b, err := os.ReadFile(logPath)
	if err != nil {
		t.Fatalf("log file: %v", err)
	}
	if !strings.Contains(string(b), "primary_failed") {
		t.Errorf("log missing event: %s", b)
	}
	if !strings.Contains(strings.ToLower(string(b)), "deadline exceeded") {
		t.Errorf("log missing reason: %s", b)
	}
	if !strings.HasSuffix(string(b), "\n") {
		t.Errorf("log should end with newline: %q", b)
	}
}

// netDialRefused returns a net.Error by attempting to dial a closed port.
func netDialRefused() (string, error) {
	ln, _ := net.Listen("tcp", "127.0.0.1:0")
	addr := ln.Addr().String()
	_ = ln.Close()
	_, err := net.DialTimeout("tcp", addr, 100*time.Millisecond)
	return addr, err
}
