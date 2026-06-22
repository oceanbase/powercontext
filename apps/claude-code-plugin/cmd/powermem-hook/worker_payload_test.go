package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

func TestReadWorkerPayloadRemovesNeutralHandoffFile(t *testing.T) {
	rawTranscriptPath := "/example-home/alice/private/project/transcript.jsonl"
	payload := workerHandoffPayload{
		TranscriptPath:    rawTranscriptPath,
		SessionID:         "session-1053",
		CWD:               "project",
		Reason:            "user-finished",
		ParentScrubReport: `{"path_redactions":1}`,
	}
	b, err := json.Marshal(payload)
	if err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(t.TempDir(), "worker-payload.json")
	if err := os.WriteFile(path, b, 0o600); err != nil {
		t.Fatal(err)
	}
	t.Setenv(workerPayloadPathEnv, path)

	got, ok := readWorkerPayload()
	if !ok {
		t.Fatalf("expected worker payload to be read")
	}
	if got.TranscriptPath != rawTranscriptPath || got.SessionID != payload.SessionID || got.CWD != payload.CWD || got.Reason != payload.Reason {
		t.Fatalf("worker payload mismatch: got %+v want %+v", got, payload)
	}
	if _, err := os.Stat(path); !os.IsNotExist(err) {
		t.Fatalf("worker payload file was not removed, stat err=%v", err)
	}
}
