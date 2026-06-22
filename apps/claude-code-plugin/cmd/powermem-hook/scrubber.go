package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
)

const (
	redactedSecret = "<redacted:secret>"
	redactedPII    = "<redacted:pii>"
	omittedPath    = "<path omitted>"

	parentScrubReportEnv = "POWERMEM_WORKER_PARENT_SCRUB_REPORT"
)

type hookPrivacyConfig struct {
	Enabled            bool
	PrivacyLevel       string
	SecretAction       string
	PathPrivacy        string
	SearchSecretPolicy string
}

type scrubReport struct {
	SecretRedactions int `json:"secret_redactions"`
	PIIRedactions    int `json:"pii_redactions"`
	PathRedactions   int `json:"path_redactions"`
}

func (r scrubReport) total() int {
	return r.SecretRedactions + r.PIIRedactions + r.PathRedactions
}

func (r *scrubReport) merge(other scrubReport) {
	r.SecretRedactions += other.SecretRedactions
	r.PIIRedactions += other.PIIRedactions
	r.PathRedactions += other.PathRedactions
}

func defaultHookPrivacyConfig() hookPrivacyConfig {
	return hookPrivacyConfig{
		Enabled:            true,
		PrivacyLevel:       "standard",
		SecretAction:       "redact",
		PathPrivacy:        "home",
		SearchSecretPolicy: "skip",
	}
}

func loadHookPrivacyConfig() hookPrivacyConfig {
	cfg := defaultHookPrivacyConfig()
	switch strings.ToLower(strings.TrimSpace(os.Getenv("POWERMEM_HOOK_SCRUB"))) {
	case "0", "false", "no", "off":
		cfg.Enabled = false
	case "1", "true", "yes", "on", "":
		cfg.Enabled = true
	}
	switch v := strings.ToLower(strings.TrimSpace(os.Getenv("POWERMEM_HOOK_PRIVACY_LEVEL"))); v {
	case "standard", "strict":
		cfg.PrivacyLevel = v
	}
	switch v := strings.ToLower(strings.TrimSpace(os.Getenv("POWERMEM_HOOK_SECRET_ACTION"))); v {
	case "redact", "block":
		cfg.SecretAction = v
	}
	switch v := strings.ToLower(strings.TrimSpace(os.Getenv("POWERMEM_HOOK_PATH_PRIVACY"))); v {
	case "home", "basename", "omit", "full":
		cfg.PathPrivacy = v
	}
	switch v := strings.ToLower(strings.TrimSpace(os.Getenv("POWERMEM_HOOK_SEARCH_SECRET_POLICY"))); v {
	case "skip", "redact", "off":
		cfg.SearchSecretPolicy = v
	}
	return cfg
}

func encodeScrubReport(report scrubReport) string {
	if report.total() == 0 {
		return ""
	}
	b, err := json.Marshal(report)
	if err != nil {
		return ""
	}
	return string(b)
}

func decodeScrubReport(raw string) scrubReport {
	var report scrubReport
	if strings.TrimSpace(raw) == "" {
		return report
	}
	_ = json.Unmarshal([]byte(raw), &report)
	return report
}

func shouldBlockWrite(cfg hookPrivacyConfig, report scrubReport) bool {
	return cfg.Enabled && cfg.SecretAction == "block" && report.SecretRedactions > 0
}

func scrubPromptForSearch(prompt string, cfg hookPrivacyConfig) (string, bool) {
	if !cfg.Enabled {
		return prompt, true
	}
	var scrubbed string
	var report scrubReport
	if cfg.SearchSecretPolicy == "off" {
		scrubbed, report = scrubTextWithoutPromptSecretPolicy(prompt, cfg)
	} else {
		scrubbed, report = scrubText(prompt, cfg)
	}
	_, highConfidenceSecrets := applyHighConfidenceSecretRedactions(prompt)
	_, looseBearerSecrets := replaceAllWithCount(bearerTokenRE, prompt, "$1"+redactedSecret)
	if cfg.SearchSecretPolicy == "skip" && (highConfidenceSecrets > 0 || report.SecretRedactions > looseBearerSecrets) {
		return "", false
	}
	if strings.TrimSpace(scrubbed) == "" {
		return "", false
	}
	return scrubbed, true
}

func scrubCompactForWorker(summary string, cwd string, cfg hookPrivacyConfig) (string, string, scrubReport, bool) {
	var report scrubReport
	if !cfg.Enabled {
		return summary, cwd, report, true
	}
	scrubbedSummary, summaryReport := scrubText(summary, cfg)
	report.merge(summaryReport)
	scrubbedCWD, cwdReport := scrubMetadataString("cwd", cwd, cfg)
	report.merge(cwdReport)
	if shouldBlockWrite(cfg, report) {
		return "", "", report, false
	}
	return scrubbedSummary, scrubbedCWD, report, true
}

func scrubCWDForWorker(cwd string, cfg hookPrivacyConfig) (string, scrubReport, bool) {
	var report scrubReport
	if !cfg.Enabled {
		return cwd, report, true
	}
	scrubbedCWD, cwdReport := scrubMetadataString("cwd", cwd, cfg)
	report.merge(cwdReport)
	if shouldBlockWrite(cfg, report) {
		return "", report, false
	}
	return scrubbedCWD, report, true
}

func scrubMemoryPayload(content string, meta map[string]any, cfg hookPrivacyConfig, parentReport scrubReport) (string, map[string]any, bool) {
	if !cfg.Enabled {
		return content, cloneMetadata(meta), false
	}
	scrubbedContent, contentReport := scrubText(content, cfg)
	scrubbedMeta, metaReport := scrubMetadata(meta, cfg)
	report := parentReport
	report.merge(contentReport)
	report.merge(metaReport)
	if shouldBlockWrite(cfg, report) {
		return "", nil, true
	}
	if scrubbedMeta == nil {
		scrubbedMeta = make(map[string]any)
	}
	scrubbedMeta["privacy"] = map[string]any{
		"scrubbed":      report.total() > 0,
		"level":         cfg.PrivacyLevel,
		"secret_action": cfg.SecretAction,
		"path_privacy":  cfg.PathPrivacy,
		"redactions": map[string]any{
			"secrets": report.SecretRedactions,
			"pii":     report.PIIRedactions,
			"paths":   report.PathRedactions,
			"total":   report.total(),
		},
	}
	return scrubbedContent, scrubbedMeta, false
}

func scrubMetadata(meta map[string]any, cfg hookPrivacyConfig) (map[string]any, scrubReport) {
	if meta == nil {
		return nil, scrubReport{}
	}
	out := make(map[string]any, len(meta))
	var report scrubReport
	for k, v := range meta {
		scrubbed, valueReport := scrubMetadataValue(k, v, cfg)
		out[k] = scrubbed
		report.merge(valueReport)
	}
	return out, report
}

func scrubMetadataValue(key string, value any, cfg hookPrivacyConfig) (any, scrubReport) {
	switch v := value.(type) {
	case string:
		return scrubMetadataString(key, v, cfg)
	case map[string]any:
		return scrubMetadata(v, cfg)
	case []any:
		out := make([]any, len(v))
		var report scrubReport
		for i, el := range v {
			scrubbed, valueReport := scrubMetadataValue(key, el, cfg)
			out[i] = scrubbed
			report.merge(valueReport)
		}
		return out, report
	default:
		return value, scrubReport{}
	}
}

func scrubMetadataString(key string, value string, cfg hookPrivacyConfig) (string, scrubReport) {
	if !cfg.Enabled {
		return value, scrubReport{}
	}
	out := value
	var report scrubReport
	if isPathMetadataKey(key) {
		var changed bool
		originalReport := scrubOriginalPathReport(out, cfg)
		out, changed = applyPathPrivacyToPath(out, cfg)
		if changed {
			report.PathRedactions++
			report.merge(originalReport)
			out = redactPathReplacementValue(out, cfg)
			return out, report
		}
	}
	scrubbed, textReport := scrubText(out, cfg)
	report.merge(textReport)
	return scrubbed, report
}

func scrubPathForDisplay(path string, cfg hookPrivacyConfig) string {
	out, _ := scrubMetadataString("path", path, cfg)
	return out
}

func scrubTextForDisplay(text string, cfg hookPrivacyConfig) string {
	out, _ := scrubText(text, cfg)
	return out
}

func cloneMetadata(meta map[string]any) map[string]any {
	if meta == nil {
		return nil
	}
	out := make(map[string]any, len(meta))
	for k, v := range meta {
		out[k] = v
	}
	return out
}

func isPathMetadataKey(key string) bool {
	key = strings.ToLower(key)
	return key == "cwd" ||
		key == "file" ||
		key == "path" ||
		strings.HasSuffix(key, "_path") ||
		strings.HasSuffix(key, "path")
}

var (
	envAssignmentRE      = regexp.MustCompile(`(?im)\b((?:[A-Z0-9_]+_)?(?:API[_-]?KEY|SECRET|TOKEN|PASSWORD|PASSWD|AUTH)(?:_[A-Z0-9_]+)?)\s*=\s*("[^"\r\n]{4,}"|'[^'\r\n]{4,}'|[^\s\r\n]{4,})`)
	doubleQuotedKVRE     = regexp.MustCompile(`(?im)(["']?(?:api[_-]?key|access[_-]?token|auth[_-]?token|refresh[_-]?token|id[_-]?token|token|client[_-]?secret|secret|password|passwd|private[_-]?key)["']?\s*[:=]\s*")[^"\r\n]{4,}(")`)
	singleQuotedKVRE     = regexp.MustCompile(`(?im)(["']?(?:api[_-]?key|access[_-]?token|auth[_-]?token|refresh[_-]?token|id[_-]?token|token|client[_-]?secret|secret|password|passwd|private[_-]?key)["']?\s*[:=]\s*')[^'\r\n]{4,}(')`)
	yamlKVRE             = regexp.MustCompile(`(?im)^(\s*(?:api[_-]?key|access[_-]?token|auth[_-]?token|refresh[_-]?token|id[_-]?token|token|client[_-]?secret|secret|password|passwd|private[_-]?key)\s*:\s*)[^\s#][^\r\n]*`)
	authHeaderRE         = regexp.MustCompile(`(?im)\b(Authorization\s*:\s*(?:Bearer|Basic)\s+)[A-Za-z0-9._~+/=-]{8,}`)
	bearerTokenRE        = regexp.MustCompile(`(?i)\b(Bearer\s+)[A-Za-z0-9._~+/=-]{8,}`)
	urlUserinfoRE        = regexp.MustCompile(`(?i)\b([a-z][a-z0-9+.-]*://)([^/\s@]+@)`)
	queryParamRE         = regexp.MustCompile(`(?i)([?&](?:api[_-]?key|access[_-]?token|auth[_-]?token|refresh[_-]?token|id[_-]?token|token|password|passwd|pwd)=)[^&#\s]+`)
	prefixedTokenRE      = regexp.MustCompile(`\b(?:sk-[A-Za-z0-9_-]{20,}|github_pat_[A-Za-z0-9_]{20,}|gh[pousr]_[A-Za-z0-9_]{20,}|ya29\.[A-Za-z0-9._-]{8,}|AKIA[0-9A-Z]{16}|xox[a-z]-[A-Za-z0-9-]{20,}|xapp-[A-Za-z0-9-]{20,})\b`)
	jwtRE                = regexp.MustCompile(`\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b`)
	longTokenRE          = regexp.MustCompile(`\b[A-Za-z0-9][A-Za-z0-9_+-]{39,}\b`)
	emailRE              = regexp.MustCompile(`\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b`)
	phoneRE              = regexp.MustCompile(`(?:\+?\d[\d .()/\-]{8,}\d)`)
	keyedPathValueRE     = regexp.MustCompile(`(?im)\b((?:PWD|cwd|file|path|transcript_path|[A-Z0-9_]+_path)=)([^\s"'<>]+)`)
	quotedKeyedPathRE    = regexp.MustCompile(`(["'](?:cwd|file|path|transcript_path)["']\s*[:=]\s*["'])([^"'\r\n]+)(["'])`)
	yamlKeyedPathRE      = regexp.MustCompile(`(?im)^(\s*(?:cwd|file|path|transcript_path)\s*:\s*)([^\r\n#][^\r\n]*)`)
	quotedPWDPathValueRE = regexp.MustCompile(`(["']PWD["']\s*[:=]\s*["'])([^"'\r\n]+)(["'])`)
	yamlPWDPathValueRE   = regexp.MustCompile(`(?m)^(\s*PWD\s*:\s*)([^\r\n#][^\r\n]*)`)
	unixSpacedPathRE     = regexp.MustCompile(`/(?:Users|home|tmp|var|data|workspace|workspaces|repo|private|mnt|opt|srv|Volumes|nix|usr|etc|media|Applications)(?:/[^\s/="'\r\n<>()[\]{}]+)*/[^.\s/="'\r\n<>()[\]{}]+(?: [^.\s/="'\r\n<>()[\]{}]+)+(?:/[^\s/="'\r\n<>()[\]{}]+)*\.[A-Za-z0-9]{1,10}(?:\?[^="'#\s<>()[\]{}]+)?`)
	unixSpacedDirRE      = regexp.MustCompile(`/(?:Users|home|tmp|var|data|workspace|workspaces|repo|private|mnt|opt|srv|Volumes|nix|usr|etc|media|Applications)(?:/[^\s/="'\r\n<>()[\]{}]+)*/[^.\s/="'\r\n<>()[\]{}]+(?: [^.\s/="'\r\n<>()[\]{}]+)+(?:/[^\s/="'\r\n<>()[\]{}]+)*`)
	unixPathRE           = regexp.MustCompile(`/(?:Users|home|tmp|var|data|workspace|workspaces|repo|private|mnt|opt|srv|Volumes|nix|usr|etc|media|Applications)(?:/[^\s"'<>()[\]{}]+)+`)
	genericUnixPathRE    = regexp.MustCompile(`/(?:[A-Za-z0-9._+~@-]+)(?:/[A-Za-z0-9._+~@-]+){2,}(?:\?[^="'#\s<>()[\]{}]+)?`)
	windowsSpacedPathRE  = regexp.MustCompile(`[A-Za-z]:\\[^\s"'\r\n<>|]+(?:\\[^\s"'\r\n<>|]+)*\\[^.\\\s"'\r\n<>|]+(?: [^.\\\s"'\r\n<>|]+)+(?:\\[^\s"'\r\n<>|]+)*\.[A-Za-z0-9]{1,10}(?:\?[^"'#\s<>|]+)?`)
	windowsSpacedDirRE   = regexp.MustCompile(`[A-Za-z]:\\[^"'\r\n<>|]* [^"'\r\n<>|]*\\[^"'\r\n<>|]+(?:\\[^"'\r\n<>|]+)*`)
	windowsUNCPathRE     = regexp.MustCompile(`\\\\[^\\\s"'<>|]+\\[^\\\r\n"'<>|]+(?:\\[^\\\r\n"'<>|]+)*`)
	windowsPathRE        = regexp.MustCompile(`[A-Za-z]:\\[^\s"'<>|]+(?:\\[^\s"'<>|]+)*`)
	windowsSlashPathRE   = regexp.MustCompile(`[A-Za-z]:/[^\s"'<>|]+(?:/[^\s"'<>|]+)*`)
	privateKeyRE         = regexp.MustCompile(`(?is)-----BEGIN [^-]*` + regexp.QuoteMeta("PRIVATE"+" KEY") + `-----.*?-----END [^-]*` + regexp.QuoteMeta("PRIVATE"+" KEY") + `-----`)
)

func scrubText(input string, cfg hookPrivacyConfig) (string, scrubReport) {
	if !cfg.Enabled || input == "" {
		return input, scrubReport{}
	}
	out := input
	var report scrubReport
	var pathCount int
	var protected []protectedText
	out, pathCount, protected = scrubPathsInTextProtected(out, cfg)
	report.PathRedactions += pathCount
	var protectedReport scrubReport
	protected, protectedReport = scrubProtectedPathValues(protected, cfg)
	report.merge(protectedReport)
	var secretCount int
	out, secretCount = applySecretRedactions(out)
	report.SecretRedactions += secretCount
	if cfg.PrivacyLevel == "strict" {
		var pii int
		out, pii = replaceAllWithCount(emailRE, out, redactedPII)
		report.PIIRedactions += pii
		out, pii = replaceAllStringFuncWithCount(phoneRE, out, func(match string) (string, bool) {
			if countDigits(match) < 10 {
				return match, false
			}
			return redactedPII, true
		})
		report.PIIRedactions += pii
	}
	out = restoreProtectedText(out, protected)
	return out, report
}

func scrubTextWithoutPromptSecretPolicy(input string, cfg hookPrivacyConfig) (string, scrubReport) {
	if !cfg.Enabled || input == "" {
		return input, scrubReport{}
	}
	out := input
	var report scrubReport
	var pathCount int
	var protected []protectedText
	out, pathCount, protected = scrubPathsInTextProtected(out, cfg)
	report.PathRedactions += pathCount
	var protectedReport scrubReport
	protected, protectedReport = scrubProtectedPathValues(protected, cfg)
	report.merge(protectedReport)
	if cfg.PrivacyLevel == "strict" {
		var pii int
		out, pii = replaceAllWithCount(emailRE, out, redactedPII)
		report.PIIRedactions += pii
		out, pii = replaceAllStringFuncWithCount(phoneRE, out, func(match string) (string, bool) {
			if countDigits(match) < 10 {
				return match, false
			}
			return redactedPII, true
		})
		report.PIIRedactions += pii
	}
	out = restoreProtectedText(out, protected)
	return out, report
}

func applySecretRedactions(input string) (string, int) {
	out := input
	total := 0
	var count int
	out, count = replaceAllWithCount(privateKeyRE, out, redactedSecret)
	total += count
	out, count = replaceAllWithCount(envAssignmentRE, out, "$1="+redactedSecret)
	total += count
	out, count = replaceAllWithCount(doubleQuotedKVRE, out, "$1"+redactedSecret+"$2")
	total += count
	out, count = replaceAllWithCount(singleQuotedKVRE, out, "$1"+redactedSecret+"$2")
	total += count
	out, count = replaceAllWithCount(yamlKVRE, out, "$1"+redactedSecret)
	total += count
	out, count = replaceAllWithCount(authHeaderRE, out, "$1"+redactedSecret)
	total += count
	out, count = replaceAllWithCount(bearerTokenRE, out, "$1"+redactedSecret)
	total += count
	out, count = replaceAllWithCount(urlUserinfoRE, out, "$1"+redactedSecret+"@")
	total += count
	out, count = replaceAllWithCount(queryParamRE, out, "$1"+redactedSecret)
	total += count
	out, count = replaceAllWithCount(prefixedTokenRE, out, redactedSecret)
	total += count
	out, count = replaceAllWithCount(jwtRE, out, redactedSecret)
	total += count
	out, count = replaceLongTokensWithCount(out)
	total += count
	return out, total
}

func applyHighConfidenceSecretRedactions(input string) (string, int) {
	out := input
	total := 0
	var count int
	out, count = replaceAllWithCount(privateKeyRE, out, redactedSecret)
	total += count
	out, count = replaceAllWithCount(envAssignmentRE, out, "$1="+redactedSecret)
	total += count
	out, count = replaceAllWithCount(doubleQuotedKVRE, out, "$1"+redactedSecret+"$2")
	total += count
	out, count = replaceAllWithCount(singleQuotedKVRE, out, "$1"+redactedSecret+"$2")
	total += count
	out, count = replaceAllWithCount(yamlKVRE, out, "$1"+redactedSecret)
	total += count
	out, count = replaceAllWithCount(authHeaderRE, out, "$1"+redactedSecret)
	total += count
	out, count = replaceAllWithCount(urlUserinfoRE, out, "$1"+redactedSecret+"@")
	total += count
	out, count = replaceAllWithCount(queryParamRE, out, "$1"+redactedSecret)
	total += count
	out, count = replaceAllWithCount(prefixedTokenRE, out, redactedSecret)
	total += count
	out, count = replaceAllWithCount(jwtRE, out, redactedSecret)
	total += count
	return out, total
}

func replaceAllWithCount(re *regexp.Regexp, input string, replacement string) (string, int) {
	count := 0
	out := re.ReplaceAllStringFunc(input, func(match string) string {
		if strings.Contains(match, redactedSecret) ||
			strings.Contains(match, redactedPII) ||
			strings.Contains(match, omittedPath) {
			return match
		}
		count++
		return re.ReplaceAllString(match, replacement)
	})
	return out, count
}

func replaceAllStringFuncWithCount(re *regexp.Regexp, input string, fn func(string) (string, bool)) (string, int) {
	count := 0
	out := re.ReplaceAllStringFunc(input, func(match string) string {
		replacement, ok := fn(match)
		if !ok {
			return match
		}
		count++
		return replacement
	})
	return out, count
}

func replaceLongTokensWithCount(input string) (string, int) {
	matches := longTokenRE.FindAllStringIndex(input, -1)
	if len(matches) == 0 {
		return input, 0
	}
	var b strings.Builder
	b.Grow(len(input))
	count := 0
	last := 0
	for _, span := range matches {
		start, end := span[0], span[1]
		match := input[start:end]
		b.WriteString(input[last:start])
		if looksLikeLongToken(match) && !looksLikePathSegment(input, start, end) {
			b.WriteString(redactedSecret)
			count++
		} else {
			b.WriteString(match)
		}
		last = end
	}
	b.WriteString(input[last:])
	return b.String(), count
}

func redactLongTokensInPathValue(input string) (string, int) {
	matches := longTokenRE.FindAllStringIndex(input, -1)
	if len(matches) == 0 {
		return input, 0
	}
	var b strings.Builder
	b.Grow(len(input))
	count := 0
	last := 0
	for _, span := range matches {
		start, end := span[0], span[1]
		match := input[start:end]
		b.WriteString(input[last:start])
		if looksLikeLongToken(match) && !looksLikeReadablePathBasename(match) {
			b.WriteString(redactedSecret)
			count++
		} else {
			b.WriteString(match)
		}
		last = end
	}
	b.WriteString(input[last:])
	return b.String(), count
}

func looksLikeReadablePathBasename(value string) bool {
	parts := strings.FieldsFunc(value, func(r rune) bool {
		return r == '_' || r == '-' || r == '+' || r == '.'
	})
	wordish := 0
	for _, part := range parts {
		if len(part) < 2 {
			continue
		}
		alpha, digit := 0, 0
		for _, r := range part {
			switch {
			case (r >= 'a' && r <= 'z') || (r >= 'A' && r <= 'Z'):
				alpha++
			case r >= '0' && r <= '9':
				digit++
			}
		}
		if alpha >= 2 && digit == 0 {
			wordish++
		}
	}
	return wordish >= 3
}

func looksLikePathSegment(input string, start int, end int) bool {
	if start > 0 && (input[start-1] == '/' || input[start-1] == '\\') {
		return true
	}
	if end < len(input) && input[end] == '.' {
		extEnd := end + 1
		for extEnd < len(input) && isAlphaNum(input[extEnd]) && extEnd-end <= 12 {
			extEnd++
		}
		extLen := extEnd - end - 1
		if extLen >= 1 && extLen <= 10 {
			return true
		}
	}
	return false
}

func isAlphaNum(ch byte) bool {
	return (ch >= 'a' && ch <= 'z') ||
		(ch >= 'A' && ch <= 'Z') ||
		(ch >= '0' && ch <= '9')
}

func looksLikeLongToken(value string) bool {
	if len(value) < 40 {
		return false
	}
	if strings.ContainsAny(value, `/\`) {
		return false
	}
	if isHex(value) {
		return false
	}
	hasLower, hasUpper, hasDigit, hasStrongSymbol := false, false, false, false
	for _, r := range value {
		switch {
		case r >= 'a' && r <= 'z':
			hasLower = true
		case r >= 'A' && r <= 'Z':
			hasUpper = true
		case r >= '0' && r <= '9':
			hasDigit = true
		case strings.ContainsRune("_+", r):
			hasStrongSymbol = true
		case r == '-':
		default:
			return false
		}
	}
	if strings.HasPrefix(value, "sk-") && len(value) >= 24 {
		return true
	}
	classes := 0
	for _, ok := range []bool{hasLower, hasUpper, hasDigit, hasStrongSymbol} {
		if ok {
			classes++
		}
	}
	return len(value) >= 48 && classes >= 3 && hasStrongSymbol
}

func isHex(value string) bool {
	if value == "" {
		return false
	}
	for _, r := range value {
		if !((r >= '0' && r <= '9') || (r >= 'a' && r <= 'f') || (r >= 'A' && r <= 'F')) {
			return false
		}
	}
	return true
}

func countDigits(value string) int {
	count := 0
	for _, r := range value {
		if r >= '0' && r <= '9' {
			count++
		}
	}
	return count
}

type protectedText struct {
	token          string
	value          string
	originalReport scrubReport
}

func scrubProtectedPathValues(protected []protectedText, cfg hookPrivacyConfig) ([]protectedText, scrubReport) {
	var report scrubReport
	for i := range protected {
		report.merge(protected[i].originalReport)
		protected[i].value = redactPathReplacementValue(protected[i].value, cfg)
	}
	return protected, report
}

func redactPathReplacementValue(value string, cfg hookPrivacyConfig) string {
	out, _ := applyHighConfidenceSecretRedactions(value)
	out, _ = redactLongTokensInPathValue(out)
	if cfg.PrivacyLevel == "strict" {
		out, _ = replaceAllWithCount(emailRE, out, redactedPII)
		out, _ = replaceAllStringFuncWithCount(phoneRE, out, func(match string) (string, bool) {
			if countDigits(match) < 10 {
				return match, false
			}
			return redactedPII, true
		})
	}
	return out
}

func scrubOriginalPathReport(path string, cfg hookPrivacyConfig) scrubReport {
	var report scrubReport
	_, count := applyHighConfidenceSecretRedactions(path)
	report.SecretRedactions += count
	base := pathBase(path)
	_, baseHighConfidenceCount := applyHighConfidenceSecretRedactions(base)
	if baseHighConfidenceCount == 0 {
		_, count = redactLongTokensInPathValue(base)
		report.SecretRedactions += count
	}
	if cfg.PrivacyLevel == "strict" {
		var pii int
		_, pii = replaceAllWithCount(emailRE, path, redactedPII)
		report.PIIRedactions += pii
		_, pii = replaceAllStringFuncWithCount(phoneRE, path, func(match string) (string, bool) {
			if countDigits(match) < 10 {
				return match, false
			}
			return redactedPII, true
		})
		report.PIIRedactions += pii
	}
	return report
}

func scrubPathsInText(input string, cfg hookPrivacyConfig) (string, int) {
	out, count, protected := scrubPathsInTextProtected(input, cfg)
	return restoreProtectedText(out, protected), count
}

func scrubPathsInTextProtected(input string, cfg hookPrivacyConfig) (string, int, []protectedText) {
	if !cfg.Enabled || cfg.PathPrivacy == "full" || input == "" {
		return input, 0, nil
	}
	out := input
	count := 0
	var protected []protectedText
	if cfg.PathPrivacy == "home" {
		for _, home := range homeDirectories() {
			out, count = replaceHomePathPrefixes(out, home, cfg, count, &protected)
		}
	}
	out, count = replacePathMatches(unixSpacedDirRE, out, cfg, count, &protected)
	out, count = replacePathMatches(windowsSpacedDirRE, out, cfg, count, &protected)
	out, count = replacePathMatches(windowsUNCPathRE, out, cfg, count, &protected)
	out, count = replaceQuotedPWDPathMatches(out, cfg, count, &protected)
	out, count = replaceYAMLPWDPathMatches(out, cfg, count, &protected)
	out, count = replaceQuotedKeyedPathMatches(out, cfg, count, &protected)
	out, count = replaceYAMLKeyedPathMatches(out, cfg, count, &protected)
	out, count = replaceKeyedPathValueMatches(out, cfg, count, &protected)
	out, count = replacePathMatches(unixSpacedPathRE, out, cfg, count, &protected)
	out, count = replacePathMatches(windowsSpacedPathRE, out, cfg, count, &protected)
	out, count = replacePathMatches(unixPathRE, out, cfg, count, &protected)
	out, count = replaceGenericUnixPathMatches(out, cfg, count, &protected)
	out, count = replacePathMatches(windowsPathRE, out, cfg, count, &protected)
	out, count = replacePathMatches(windowsSlashPathRE, out, cfg, count, &protected)
	return out, count, protected
}

func replaceHomePathPrefixes(input string, home string, cfg hookPrivacyConfig, initialCount int, protected *[]protectedText) (string, int) {
	home = normalizedHomePrefix(home)
	if home == "" {
		return input, initialCount
	}
	count := initialCount
	var b strings.Builder
	b.Grow(len(input))
	last := 0
	searchFrom := 0
	for {
		idx := strings.Index(input[searchFrom:], home)
		if idx < 0 {
			break
		}
		start := searchFrom + idx
		end := start + len(home)
		if !isPathBoundaryBefore(input, start) || !isPathBoundaryAfter(input, end) || pathMatchIsInsideURL(input, start) {
			searchFrom = end
			continue
		}
		pathEnd := homePathMatchEnd(input, end)
		path := input[start:pathEnd]
		replacement := "~" + path[len(home):]
		b.WriteString(input[last:start])
		token := "\x00powermem_path_" + strconv.Itoa(len(*protected)) + "\x00"
		originalReport := scrubOriginalPathReport(path, cfg)
		*protected = append(*protected, protectedText{token: token, value: replacement, originalReport: originalReport})
		b.WriteString(token)
		count++
		last = pathEnd
		searchFrom = pathEnd
	}
	if count == initialCount {
		return input, initialCount
	}
	b.WriteString(input[last:])
	return b.String(), count
}

func homePathMatchEnd(input string, start int) int {
	i := start
	for i < len(input) {
		if strings.ContainsRune("\t\r\n\"'`<>()[{}]", rune(input[i])) {
			break
		}
		if input[i] == ' ' && !spaceBelongsToPathSegment(input, i) {
			break
		}
		i++
	}
	return i
}

func spaceBelongsToPathSegment(input string, idx int) bool {
	nextSlash := strings.IndexByte(input[idx+1:], '/')
	if nextSlash < 0 {
		return false
	}
	between := input[idx+1 : idx+1+nextSlash]
	if between == "" {
		return false
	}
	for _, word := range []string{"and", "or", "then", "plus"} {
		if strings.EqualFold(between, word) {
			return false
		}
	}
	if strings.ContainsAny(between, "\t\r\n\"'`<>()[{}]=") {
		return false
	}
	return true
}

func replaceQuotedPWDPathMatches(input string, cfg hookPrivacyConfig, initialCount int, protected *[]protectedText) (string, int) {
	count := initialCount
	out := quotedPWDPathValueRE.ReplaceAllStringFunc(input, func(match string) string {
		parts := quotedPWDPathValueRE.FindStringSubmatch(match)
		if len(parts) != 4 {
			return match
		}
		prefix, path, suffix := parts[1], strings.TrimSpace(parts[2]), parts[3]
		if !looksAbsolutePath(path) {
			return match
		}
		replacement := pathReplacement(path, cfg)
		if replacement == path {
			return match
		}
		count++
		token := "\x00powermem_path_" + strconv.Itoa(len(*protected)) + "\x00"
		originalReport := scrubOriginalPathReport(path, cfg)
		*protected = append(*protected, protectedText{token: token, value: prefix + replacement + suffix, originalReport: originalReport})
		return token
	})
	return out, count
}

func replaceYAMLPWDPathMatches(input string, cfg hookPrivacyConfig, initialCount int, protected *[]protectedText) (string, int) {
	count := initialCount
	out := yamlPWDPathValueRE.ReplaceAllStringFunc(input, func(match string) string {
		parts := yamlPWDPathValueRE.FindStringSubmatch(match)
		if len(parts) != 3 {
			return match
		}
		prefix, rawValue := parts[1], strings.TrimSpace(parts[2])
		valuePrefix, path, valueSuffix := splitPathLiteral(rawValue)
		if !looksAbsolutePath(path) {
			return match
		}
		replacement := pathReplacement(path, cfg)
		if replacement == path {
			return match
		}
		count++
		token := "\x00powermem_path_" + strconv.Itoa(len(*protected)) + "\x00"
		originalReport := scrubOriginalPathReport(path, cfg)
		*protected = append(*protected, protectedText{token: token, value: prefix + valuePrefix + replacement + valueSuffix, originalReport: originalReport})
		return token
	})
	return out, count
}

func replaceQuotedKeyedPathMatches(input string, cfg hookPrivacyConfig, initialCount int, protected *[]protectedText) (string, int) {
	count := initialCount
	out := quotedKeyedPathRE.ReplaceAllStringFunc(input, func(match string) string {
		parts := quotedKeyedPathRE.FindStringSubmatch(match)
		if len(parts) != 4 {
			return match
		}
		prefix, path, suffix := parts[1], strings.TrimSpace(parts[2]), parts[3]
		if !looksAbsolutePath(path) {
			return match
		}
		replacement := pathReplacement(path, cfg)
		if replacement == path {
			return match
		}
		count++
		token := "\x00powermem_path_" + strconv.Itoa(len(*protected)) + "\x00"
		originalReport := scrubOriginalPathReport(path, cfg)
		*protected = append(*protected, protectedText{token: token, value: prefix + replacement + suffix, originalReport: originalReport})
		return token
	})
	return out, count
}

func replaceYAMLKeyedPathMatches(input string, cfg hookPrivacyConfig, initialCount int, protected *[]protectedText) (string, int) {
	count := initialCount
	out := yamlKeyedPathRE.ReplaceAllStringFunc(input, func(match string) string {
		parts := yamlKeyedPathRE.FindStringSubmatch(match)
		if len(parts) != 3 {
			return match
		}
		prefix, rawValue := parts[1], strings.TrimSpace(parts[2])
		valuePrefix, path, valueSuffix := splitPathLiteral(rawValue)
		if !looksAbsolutePath(path) {
			return match
		}
		replacement := pathReplacement(path, cfg)
		if replacement == path {
			return match
		}
		count++
		token := "\x00powermem_path_" + strconv.Itoa(len(*protected)) + "\x00"
		originalReport := scrubOriginalPathReport(path, cfg)
		*protected = append(*protected, protectedText{token: token, value: prefix + valuePrefix + replacement + valueSuffix, originalReport: originalReport})
		return token
	})
	return out, count
}

func splitPathLiteral(raw string) (string, string, string) {
	raw = strings.TrimSpace(raw)
	if raw == "" {
		return "", "", ""
	}
	if raw[0] == '"' || raw[0] == '\'' {
		quote := raw[0]
		if idx := strings.LastIndexByte(raw[1:], quote); idx >= 0 {
			end := idx + 1
			return raw[:1], raw[1:end], raw[end:]
		}
	}
	if idx := strings.Index(raw, " #"); idx >= 0 {
		return "", strings.TrimSpace(raw[:idx]), raw[idx:]
	}
	return "", raw, ""
}

func replaceKeyedPathValueMatches(input string, cfg hookPrivacyConfig, initialCount int, protected *[]protectedText) (string, int) {
	count := initialCount
	out := keyedPathValueRE.ReplaceAllStringFunc(input, func(match string) string {
		parts := keyedPathValueRE.FindStringSubmatch(match)
		if len(parts) != 3 {
			return match
		}
		prefix, path := parts[1], parts[2]
		if !looksAbsolutePath(path) {
			return match
		}
		if strings.HasPrefix(path, "/") && !keyAllowsAnyAbsolutePath(prefix) && !looksLikeScrubbableUnixPath(path) {
			return match
		}
		replacement := pathReplacement(path, cfg)
		if replacement == path {
			return match
		}
		count++
		token := "\x00powermem_path_" + strconv.Itoa(len(*protected)) + "\x00"
		originalReport := scrubOriginalPathReport(path, cfg)
		*protected = append(*protected, protectedText{token: token, value: prefix + replacement, originalReport: originalReport})
		return token
	})
	return out, count
}

func replacePathMatches(re *regexp.Regexp, input string, cfg hookPrivacyConfig, initialCount int, protected *[]protectedText) (string, int) {
	matches := re.FindAllStringIndex(input, -1)
	if len(matches) == 0 {
		return input, initialCount
	}
	count := initialCount
	var b strings.Builder
	b.Grow(len(input))
	last := 0
	for _, span := range matches {
		start, end := span[0], span[1]
		if start < last {
			continue
		}
		match := input[start:end]
		b.WriteString(input[last:start])
		if !isPathBoundaryBefore(input, start) && !pathMatchStartsFileURLPath(input, start) {
			b.WriteString(match)
			last = end
			continue
		}
		if pathMatchIsInsideURL(input, start) {
			b.WriteString(match)
			last = end
			continue
		}
		core, suffix := trimPathSuffix(match)
		if core == "" {
			b.WriteString(match)
			last = end
			continue
		}
		replacement := pathReplacement(core, cfg)
		if replacement == core {
			b.WriteString(match)
			last = end
			continue
		}
		count++
		token := "\x00powermem_path_" + strconv.Itoa(len(*protected)) + "\x00"
		originalReport := scrubOriginalPathReport(core+suffix, cfg)
		*protected = append(*protected, protectedText{token: token, value: replacement + suffix, originalReport: originalReport})
		b.WriteString(token)
		last = end
	}
	b.WriteString(input[last:])
	return b.String(), count
}

func replaceGenericUnixPathMatches(input string, cfg hookPrivacyConfig, initialCount int, protected *[]protectedText) (string, int) {
	matches := genericUnixPathRE.FindAllStringIndex(input, -1)
	if len(matches) == 0 {
		return input, initialCount
	}
	count := initialCount
	var b strings.Builder
	b.Grow(len(input))
	last := 0
	for _, span := range matches {
		start, end := span[0], span[1]
		if start < last {
			continue
		}
		match := input[start:end]
		b.WriteString(input[last:start])
		if !isPathBoundaryBefore(input, start) && !pathMatchStartsFileURLPath(input, start) {
			b.WriteString(match)
			last = end
			continue
		}
		if pathMatchIsInsideURL(input, start) {
			b.WriteString(match)
			last = end
			continue
		}
		core, suffix := trimPathSuffix(match)
		if !looksLikeGenericUnixFilesystemPath(core) {
			b.WriteString(match)
			last = end
			continue
		}
		replacement := pathReplacement(core, cfg)
		if replacement == core {
			b.WriteString(match)
			last = end
			continue
		}
		count++
		token := "\x00powermem_path_" + strconv.Itoa(len(*protected)) + "\x00"
		originalReport := scrubOriginalPathReport(core+suffix, cfg)
		*protected = append(*protected, protectedText{token: token, value: replacement + suffix, originalReport: originalReport})
		b.WriteString(token)
		last = end
	}
	b.WriteString(input[last:])
	return b.String(), count
}

func looksLikeGenericUnixFilesystemPath(path string) bool {
	if !strings.HasPrefix(path, "/") || strings.HasPrefix(path, "//") {
		return false
	}
	segments := strings.Split(strings.Trim(path, "/"), "/")
	if len(segments) < 3 || isLikelyWebRouteRoot(segments[0]) {
		return false
	}
	if looksLikeScrubbableUnixPath(path) {
		return true
	}
	base := pathBase(path)
	if hasFileExtension(base) {
		return true
	}
	return len(segments) >= 4
}

func isLikelyWebRouteRoot(segment string) bool {
	switch strings.ToLower(segment) {
	case "api", "apis", "static", "assets", "asset", "css", "js", "images", "img", "fonts", "health", "metrics", "openapi", "swagger":
		return true
	default:
		return false
	}
}

func hasFileExtension(base string) bool {
	idx := strings.LastIndexByte(base, '.')
	if idx <= 0 || idx == len(base)-1 {
		return false
	}
	ext := base[idx+1:]
	if len(ext) > 10 {
		return false
	}
	for i := 0; i < len(ext); i++ {
		if !isAlphaNum(ext[i]) {
			return false
		}
	}
	return true
}

func pathMatchIsInsideURL(input string, start int) bool {
	if start > 0 && input[start-1] == '~' {
		return true
	}
	segment := pathURLPrefixSegment(input, start)
	if strings.HasPrefix(strings.ToLower(segment), "file://") {
		return false
	}
	return strings.Contains(segment, "://") || strings.Contains(segment+"/", "://")
}

func pathMatchStartsFileURLPath(input string, start int) bool {
	return strings.HasPrefix(strings.ToLower(pathURLPrefixSegment(input, start)), "file://")
}

func pathURLPrefixSegment(input string, start int) string {
	prefix := input[:start]
	segmentStart := strings.LastIndexAny(prefix, " \t\r\n\"'<>()[{}]")
	if segmentStart < 0 {
		segmentStart = 0
	} else {
		segmentStart++
	}
	return prefix[segmentStart:]
}

func normalizedHomePrefix(home string) string {
	if home == "" || home == "/" {
		return ""
	}
	home = strings.TrimRight(filepath.Clean(home), `/\`)
	if home == "." {
		return ""
	}
	return home
}

func isPathBoundaryBefore(input string, start int) bool {
	if start == 0 {
		return true
	}
	prev := input[start-1]
	return strings.ContainsRune(" \t\r\n\"'`([{<=", rune(prev))
}

func isPathBoundaryAfter(input string, end int) bool {
	if end >= len(input) {
		return true
	}
	next := input[end]
	return next == '/' || next == '\\' || strings.ContainsRune(" \t\r\n\"'`)]}>,.;:", rune(next))
}

func looksAbsolutePath(path string) bool {
	return strings.HasPrefix(path, "/") ||
		strings.HasPrefix(path, `\\`) ||
		(len(path) >= 3 && ((path[0] >= 'A' && path[0] <= 'Z') || (path[0] >= 'a' && path[0] <= 'z')) && path[1] == ':' && (path[2] == '\\' || path[2] == '/'))
}

func looksLikeScrubbableUnixPath(path string) bool {
	for _, home := range homeDirectories() {
		home = normalizedHomePrefix(home)
		if home == "" {
			continue
		}
		if path == home || strings.HasPrefix(path, home+"/") {
			return true
		}
	}
	for _, root := range []string{
		"/Users/", "/home/", "/tmp/", "/var/", "/data/", "/workspace/", "/workspaces/",
		"/repo/", "/private/", "/mnt/", "/opt/", "/srv/", "/Volumes/", "/nix/",
		"/usr/", "/etc/", "/media/", "/Applications/",
	} {
		if strings.HasPrefix(path, root) {
			return true
		}
	}
	return false
}

func keyAllowsAnyAbsolutePath(prefix string) bool {
	key := strings.TrimSuffix(strings.ToLower(prefix), "=")
	return key == "pwd" || key == "cwd" || key == "file" || key == "transcript_path"
}

func restoreProtectedText(input string, protected []protectedText) string {
	out := input
	for _, item := range protected {
		out = strings.ReplaceAll(out, item.token, item.value)
	}
	return out
}

func applyPathPrivacyToPath(path string, cfg hookPrivacyConfig) (string, bool) {
	if !cfg.Enabled || cfg.PathPrivacy == "full" || path == "" {
		return path, false
	}
	if cfg.PathPrivacy == "home" {
		for _, home := range homeDirectories() {
			if home == "" || home == "/" {
				continue
			}
			cleanHome := filepath.Clean(home)
			cleanPath := filepath.Clean(path)
			if cleanPath == cleanHome {
				return "~", true
			}
			for _, sep := range []string{"/", `\`} {
				prefix := strings.TrimRight(cleanHome, `/\`) + sep
				if strings.HasPrefix(cleanPath, prefix) {
					return "~" + sep + strings.TrimPrefix(cleanPath, prefix), true
				}
			}
		}
		if looksAbsolutePath(path) {
			replacement := pathReplacement(path, cfg)
			return replacement, replacement != path
		}
		return path, false
	}
	replacement := pathReplacement(path, cfg)
	return replacement, replacement != path
}

func pathReplacement(path string, cfg hookPrivacyConfig) string {
	switch cfg.PathPrivacy {
	case "home":
		if looksAbsolutePath(path) {
			base := pathBase(path)
			if base != "" {
				return base
			}
		}
		return path
	case "basename":
		return pathBase(path)
	case "omit":
		return omittedPath
	default:
		return path
	}
}

func pathBase(path string) string {
	path = strings.TrimRight(path, `/\`)
	if path == "" {
		return path
	}
	idx := strings.LastIndexAny(path, `/\`)
	if idx < 0 {
		return path
	}
	return path[idx+1:]
}

func trimPathSuffix(path string) (string, string) {
	trimmed := strings.TrimRight(path, ".,;:)]}")
	return trimmed, strings.TrimPrefix(path, trimmed)
}

func homeDirectories() []string {
	var dirs []string
	if h := os.Getenv("HOME"); h != "" {
		dirs = append(dirs, h)
	}
	if h := os.Getenv("USERPROFILE"); h != "" {
		dirs = append(dirs, h)
	}
	return dirs
}
