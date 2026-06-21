package main

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"
)

func runPollLoop() {
	base := baseURL()
	root := filepath.Clean(os.Getenv("POWERMEM_WATCH_ROOT"))
	if root == "" || root == "." {
		var err error
		root, err = os.Getwd()
		if err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
	}
	if st, err := os.Stat(root); err != nil || !st.IsDir() {
		fmt.Fprintf(os.Stderr, "POWERMEM_WATCH_ROOT is not a directory: %s\n", root)
		os.Exit(1)
	}

	interval := 20.0
	if s := strings.TrimSpace(os.Getenv("POWERMEM_POLL_INTERVAL")); s != "" {
		if v, err := strconv.ParseFloat(s, 64); err == nil && v >= 5 {
			interval = v
		}
	}
	sufRaw := os.Getenv("POWERMEM_WATCH_SUFFIXES")
	if strings.TrimSpace(sufRaw) == "" {
		sufRaw = ".md,.mdx,.txt"
	}
	suffixes := parseSuffixes(sufRaw)
	ignRaw := os.Getenv("POWERMEM_WATCH_IGNORE_DIRS")
	if strings.TrimSpace(ignRaw) == "" {
		ignRaw = ".git,node_modules,.venv,dist,build,target,.claude"
	}
	ignore := parseIgnoreDirs(ignRaw)

	statePath := filepath.Join(root, ".powermem-watcher-state.json")
	state := loadState(statePath)

	fmt.Fprintf(os.Stdout, "PowerMem file watcher: root=%s interval=%gs -> %s\n", root, interval, strings.TrimRight(base, "/"))

	for {
		current := scanMtimes(root, suffixes, ignore)
		changed := false
		for pathStr, mtimeNs := range current {
			if state[pathStr] >= mtimeNs {
				continue
			}
			if err := runWorkerFileSync(pathStr); err == nil {
				state[pathStr] = mtimeNs
				changed = true
				fmt.Fprintf(os.Stdout, "uploaded: %s\n", pathStr)
			}
		}
		if changed {
			saveState(statePath, state)
		}
		time.Sleep(time.Duration(interval * float64(time.Second)))
	}
}

func parseSuffixes(raw string) map[string]struct{} {
	out := make(map[string]struct{})
	for _, p := range strings.Split(raw, ",") {
		p = strings.TrimSpace(strings.ToLower(p))
		if p == "" {
			continue
		}
		if !strings.HasPrefix(p, ".") {
			p = "." + p
		}
		out[p] = struct{}{}
	}
	return out
}

func parseIgnoreDirs(raw string) map[string]struct{} {
	out := make(map[string]struct{})
	for _, p := range strings.Split(raw, ",") {
		p = strings.TrimSpace(p)
		if p != "" {
			out[p] = struct{}{}
		}
	}
	return out
}

func scanMtimes(root string, suffixes map[string]struct{}, ignore map[string]struct{}) map[string]int64 {
	out := make(map[string]int64)
	_ = filepath.WalkDir(root, func(path string, d os.DirEntry, err error) error {
		if err != nil {
			return nil
		}
		if d.IsDir() {
			name := d.Name()
			if name == "." || name == "" {
				return nil
			}
			if _, skip := ignore[name]; skip {
				return filepath.SkipDir
			}
			return nil
		}
		ext := strings.ToLower(filepath.Ext(path))
		if _, ok := suffixes[ext]; !ok {
			return nil
		}
		abs, err := filepath.Abs(path)
		if err != nil {
			return nil
		}
		st, err := os.Stat(path)
		if err != nil {
			return nil
		}
		out[abs] = st.ModTime().UnixNano()
		return nil
	})
	return out
}

func loadState(path string) map[string]int64 {
	b, err := os.ReadFile(path)
	if err != nil {
		return make(map[string]int64)
	}
	var raw map[string]any
	if json.Unmarshal(b, &raw) != nil {
		return make(map[string]int64)
	}
	out := make(map[string]int64)
	for k, v := range raw {
		switch t := v.(type) {
		case float64:
			out[k] = int64(t)
		case int64:
			out[k] = t
		default:
		}
	}
	return out
}

func saveState(path string, state map[string]int64) {
	b, err := json.MarshalIndent(state, "", "  ")
	if err != nil {
		return
	}
	_ = os.WriteFile(path, b, 0o644)
}
