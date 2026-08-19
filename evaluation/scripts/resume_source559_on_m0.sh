#!/usr/bin/env bash

# Manual, fail-closed source559 retry boundary. Run on m0 as root only.
set -Eeuo pipefail

readonly api_host="100.88.99.11"
readonly api_base="http://100.88.99.11:8787"
readonly batch_id="batch-20260805-100516-338400-0000000008-49a19a0e"
readonly source559_instance="instance_gravitational__teleport-c335534e02de143508ebebc7341021d7f8656e8f"
readonly expected_head="ead5387c578d10c2b2ab9fc05a616df8fc0d0491"
readonly deploy_dir="/data/powercontext-eval/deploy/powercontext"
readonly env_file="/data/powercontext-eval/config/evaluation-console.env"
readonly worker_service="powercontext-eval-worker.service"
readonly web_service="powercontext-eval-web.service"
readonly validation_log="/tmp/pc-52e001a-verify.log"
readonly validation_exit="/tmp/pc-52e001a-verify.exit"
readonly expected_total=731
readonly expected_succeeded=560
readonly expected_queued=170

dry_run=0
claim_observation_test=0
claim_observation_status=""
claim_observation_active=""
claim_observation_running=""
log_path=""
tmp_dir=""
phase="argument parsing"
failed_line=""
env_backup=""
env_changed=0
rollback_attempted=0
retry_started=0
source559_task_id=""

usage() {
  cat <<'EOF'
Usage: resume_source559_on_m0.sh [--dry-run] [--log PATH]

Run on m0 as root. --dry-run performs read-only preflight and never edits,
restarts, retries, pauses, or resumes.
EOF
}

claim_observation_decision() {
  local status=$1
  local active=$2
  local running=$3
  [[ $active =~ ^[0-9]+$ && $running =~ ^[0-9]+$ ]] || return 2
  case $status in
    running|succeeded)
      printf 'claimed\n'
      ;;
    queued)
      if (( active == 0 && running == 0 )); then
        printf 'wait\n'
      else
        # A queued source detail plus worker activity is an ownership race,
        # not proof that a different task was claimed. Pause and reconcile
        # the durable task pages before making any ownership assertion.
        printf 'reconcile\n'
      fi
      ;;
    *)
      printf 'reconcile\n'
      ;;
  esac
}

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$*"
}

fail() {
  log "ERROR: $*"
  exit 1
}

record_error_line() {
  failed_line=$BASH_LINENO
}

api_get_to() {
  local route=$1
  local destination=$2
  curl --fail --silent --show-error --noproxy '*' --interface "$api_host" \
    --connect-timeout 3 --max-time 15 "$api_base$route" >"$destination" ||
    fail "API GET failed for $route"
}

api_get_try_to() {
  local route=$1
  local destination=$2
  curl --fail --silent --show-error --noproxy '*' --interface "$api_host" \
    --connect-timeout 3 --max-time 15 "$api_base$route" >"$destination"
}

api_post_discard() {
  local route=$1
  local body=$2
  curl --fail --silent --show-error --noproxy '*' --interface "$api_host" \
    --connect-timeout 3 --max-time 30 -X POST -H 'Content-Type: application/json' \
    --data "$body" "$api_base$route" >/dev/null ||
    fail "API POST failed for $route"
}

api_post_try_discard() {
  local route=$1
  local body=$2
  curl --fail --silent --show-error --noproxy '*' --interface "$api_host" \
    --connect-timeout 3 --max-time 30 -X POST -H 'Content-Type: application/json' \
    --data "$body" "$api_base$route" >/dev/null 2>/dev/null
}

parse_batch() {
  local file=$1
  python3 - "$file" <<'PY'
import json
import sys

try:
    payload = json.load(open(sys.argv[1], encoding="utf-8"))
    control = payload["control"]
    print(
        f"{payload['status']}\t{control['intent']}\t"
        f"{control.get('pause_reason') or 'none'}\t{int(payload['total_tasks'])}"
    )
except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
    raise SystemExit(1)
PY
}

parse_health() {
  local file=$1
  python3 - "$file" <<'PY'
import json
import sys

try:
    payload = json.load(open(sys.argv[1], encoding="utf-8"))
    print("\t".join(str(payload[key]) for key in (
        "service", "worker_lease_active", "active_task_pairs",
        "task_parallelism", "queued_tasks", "running_tasks",
    )))
except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
    raise SystemExit(1)
PY
}

parse_usage() {
  local file=$1
  python3 - "$file" <<'PY'
import json
import math
import sys

try:
    used = float(json.load(open(sys.argv[1], encoding="utf-8"))["used_percent"])
except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
    raise SystemExit(1)
if not math.isfinite(used):
    raise SystemExit(1)
print(f"{used:.2f}")
PY
}

collect_task_pages() {
  local offset=0
  local total=-1
  local page_count=0
  local metadata page_total page_limit page_offset item_count page
  rm -f "$tmp_dir"/tasks-*.json
  while :; do
    page="$tmp_dir/tasks-$offset.json"
    api_get_to "/api/batches/$batch_id/tasks?limit=200&offset=$offset" "$page"
    metadata="$(python3 - "$page" <<'PY'
import json
import sys

try:
    payload = json.load(open(sys.argv[1], encoding="utf-8"))
    print(f"{int(payload['total'])}\t{int(payload['limit'])}\t"
          f"{int(payload['offset'])}\t{len(payload['items'])}")
except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
    raise SystemExit(1)
PY
)" || fail "Task page metadata could not be parsed"
    IFS=$'\t' read -r page_total page_limit page_offset item_count <<<"$metadata"
    [[ $page_limit == 200 ]] || fail "Unexpected task page size"
    [[ $page_offset == "$offset" ]] || fail "Unexpected task page offset"
    if (( total < 0 )); then
      total=$page_total
    else
      [[ $page_total == "$total" ]] || fail "Task page totals changed"
    fi
    (( item_count > 0 || total == 0 )) || fail "Task page unexpectedly empty"
    offset=$((offset + item_count))
    page_count=$((page_count + 1))
    (( page_count <= 20 )) || fail "Task pagination exceeded safety bound"
    (( offset >= total )) && break
  done
  [[ $total == $expected_total ]] || fail "Unexpected batch task total"
}

task_summary() {
  python3 - "$tmp_dir" "$source559_instance" <<'PY'
import json
import sys
from collections import Counter
from pathlib import Path

root = Path(sys.argv[1])
instance = sys.argv[2]
items = []
for path in sorted(root.glob("tasks-*.json"), key=lambda p: int(p.stem.split("-")[-1])):
    items.extend(json.loads(path.read_text(encoding="utf-8"))["items"])
matches = [item for item in items if item.get("instance_id") == instance]
if len(matches) != 1:
    raise SystemExit(1)
source = matches[0]
counts = Counter(item.get("status") for item in items)
other_bad = sum(
    1 for item in items
    if item is not source and item.get("status") not in {"queued", "succeeded"}
)
values = (
    len(items), counts.get("succeeded", 0), counts.get("queued", 0),
    counts.get("running", 0), counts.get("failed", 0),
    counts.get("interrupted", 0), counts.get("cancelled", 0),
    source.get("task_id", ""), source.get("status", ""),
    source.get("attempt_number", ""), source.get("attempt_count", ""),
    str(bool(source.get("retryable"))).lower(), other_bad,
)
print("\t".join(str(value) for value in values))
PY
}

parse_retry() {
  local file=$1
  python3 - "$file" <<'PY'
import json
import sys

try:
    payload = json.load(open(sys.argv[1], encoding="utf-8"))
    if not isinstance(payload, list) or not payload:
        raise ValueError
    attempt = max(payload, key=lambda item: int(item["attempt_number"]))
    print("\t".join(str(attempt[key]) for key in (
        "task_id", "attempt_id", "attempt_number", "status",
    )))
except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
    raise SystemExit(1)
PY
}

parse_source_detail() {
  local file=$1
  python3 - "$file" <<'PY'
import json
import sys

try:
    task = json.load(open(sys.argv[1], encoding="utf-8"))["task"]
    print("\t".join(str(task[key]) for key in (
        "task_id", "status", "attempt_number", "attempt_count",
    )))
except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
    raise SystemExit(1)
PY
}

monotonic_deadline() {
  python3 - <<'PY'
import time
print(f"{time.monotonic() + 60.0:.9f}")
PY
}

before_monotonic_deadline() {
  python3 - "$1" <<'PY'
import sys
import time
raise SystemExit(0 if time.monotonic() < float(sys.argv[1]) else 1)
PY
}

read_parallelism() {
  python3 - "$env_file" <<'PY'
from pathlib import Path
import re
import sys

try:
    data = Path(sys.argv[1]).read_bytes()
except OSError:
    raise SystemExit(1)
matches = list(re.finditer(rb"(?m)^POWERCONTEXT_EVAL_TASK_PARALLELISM=([^\r\n]*)$", data))
if len(matches) != 1:
    raise SystemExit(1)
print(matches[0].group(1).decode("ascii"))
PY
}

replace_parallelism() {
  local expected=$1
  local replacement=$2
  python3 - "$env_file" "$expected" "$replacement" <<'PY'
from pathlib import Path
import re
import os
import sys
import tempfile

path = Path(sys.argv[1])
expected = sys.argv[2].encode("ascii")
replacement = sys.argv[3].encode("ascii")
data = path.read_bytes()
matches = list(re.finditer(rb"(?m)^POWERCONTEXT_EVAL_TASK_PARALLELISM=([^\r\n]*)$", data))
if len(matches) != 1 or matches[0].group(1) != expected:
    raise SystemExit(1)
match = matches[0]
updated = data[:match.start(1)] + replacement + data[match.end(1):]
stat = path.stat()
fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.source559.", dir=path.parent)
try:
    os.fchown(fd, stat.st_uid, stat.st_gid)
    os.fchmod(fd, stat.st_mode & 0o7777)
    with os.fdopen(fd, "wb", closefd=True) as output:
        output.write(updated)
        output.flush()
        os.fsync(output.fileno())
    fd = -1
    os.replace(temporary, path)
    directory = os.open(path.parent, os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
except BaseException:
    if fd >= 0:
        os.close(fd)
    try:
        os.unlink(temporary)
    except FileNotFoundError:
        pass
    raise
PY
}

atomic_copy_file() {
  local source=$1
  local destination=$2
  local overwrite=${3:-0}
  python3 - "$source" "$destination" "$overwrite" <<'PY'
import os
import shutil
import sys
import tempfile
from pathlib import Path

source = Path(sys.argv[1])
destination = Path(sys.argv[2])
overwrite = sys.argv[3] == "1"
if destination.exists() and not overwrite:
    raise SystemExit(1)
stat = source.stat()
fd, temporary = tempfile.mkstemp(prefix=f".{destination.name}.source559.", dir=destination.parent)
try:
    os.fchown(fd, stat.st_uid, stat.st_gid)
    os.fchmod(fd, stat.st_mode & 0o7777)
    with source.open("rb") as input_file, os.fdopen(fd, "wb", closefd=True) as output:
        shutil.copyfileobj(input_file, output)
        output.flush()
        os.fsync(output.fileno())
    fd = -1
    os.replace(temporary, destination)
    directory = os.open(destination.parent, os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
except BaseException:
    if fd >= 0:
        os.close(fd)
    try:
        os.unlink(temporary)
    except FileNotFoundError:
        pass
    raise
PY
}

check_container() {
  local name=$1
  local expected_health=$2
  local state status health
  state="$(docker inspect --format '{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$name" 2>/dev/null)" ||
    fail "Required container is not inspectable: $name"
  IFS='|' read -r status health <<<"$state"
  [[ $status == running ]] || fail "Required container is not running: $name"
  if [[ $expected_health == healthy ]]; then
    [[ $health == healthy ]] || fail "Required container health is not healthy: $name"
  else
    [[ $health == healthy || $health == none ]] ||
      fail "Required container health is not acceptable: $name"
  fi
  log "container $name=running/$health"
}

check_data_resources() {
  local fs_used fs_available inode_used inode_available
  read -r fs_used fs_available < <(
    df -P /data | awk 'NR == 2 { gsub("%", "", $5); print $5, $4 }'
  )
  read -r inode_used inode_available < <(
    df -Pi /data | awk 'NR == 2 { gsub("%", "", $5); print $5, $4 }'
  )
  [[ $fs_used =~ ^[0-9]+$ && $inode_used =~ ^[0-9]+$ ]] ||
    fail "Unable to read /data resource usage"
  (( fs_used < 90 )) || fail "/data filesystem usage is too high"
  (( inode_used < 90 )) || fail "/data inode usage is too high"
  (( inode_available >= 1000000 )) || fail "/data inode headroom is too low"
  log "data fs_used=$fs_used% fs_available_blocks=$fs_available inode_used=$inode_used% inode_available=$inode_available"
}

wait_health_capacity() {
  local expected_capacity=$1
  local require_zero=$2
  local summary service lease active capacity queued running attempt
  for attempt in $(seq 1 30); do
    if systemctl is-active --quiet "$worker_service" &&
       api_get_try_to "/api/health" "$tmp_dir/health.json"; then
      if summary="$(parse_health "$tmp_dir/health.json" 2>/dev/null)"; then
        IFS=$'\t' read -r service lease active capacity queued running <<<"$summary"
        if [[ $service == ok && $capacity == "$expected_capacity" ]] &&
           { [[ $require_zero != 1 ]] || [[ $active == 0 && $running == 0 ]]; }; then
          return 0
        fi
      fi
    fi
    sleep 1
  done
  return 1
}

pause_best_effort() {
  api_post_try_discard "/api/batches/$batch_id/pause" "{}"
}

verify_batch_pause() {
  local summary status intent reason total
  api_get_to "/api/batches/$batch_id" "$tmp_dir/batch.json"
  summary="$(parse_batch "$tmp_dir/batch.json" 2>/dev/null)" ||
    fail "Batch response could not be parsed"
  IFS=$'\t' read -r status intent reason total <<<"$summary"
  [[ $intent == pause && $total == "$expected_total" ]] ||
    fail "Batch control intent or total changed"
  log "batch control=pause status=$status pause_reason=$reason"
}

verify_post_claim_state() {
  local summary service lease active capacity queued running
  local task_line total succeeded failed interrupted cancelled observed_id observed_status observed_attempt other_bad
  verify_batch_pause
  api_get_to "/api/health" "$tmp_dir/health.json"
  summary="$(parse_health "$tmp_dir/health.json" 2>/dev/null)" ||
    fail "Health response could not be parsed"
  IFS=$'\t' read -r service lease active capacity queued running <<<"$summary"
  [[ $service == ok && $capacity == 1 ]] || fail "Worker capacity is not 1 after claim"
  (( active <= 1 )) || fail "More than one active task pair was observed"
  collect_task_pages
  task_line="$(task_summary)" || fail "Post-claim task summary failed"
  IFS=$'\t' read -r total succeeded queued running failed interrupted cancelled \
    observed_id observed_status observed_attempt _ observed_retryable other_bad <<<"$task_line"
  [[ $total == "$expected_total" && $succeeded == "$expected_succeeded" ]] ||
    fail "Post-claim succeeded count changed"
  [[ $failed == 0 && $interrupted == 0 && $cancelled == 0 && $other_bad == 0 ]] ||
    fail "A task other than source559 changed state"
  [[ $observed_id == "$source559_task_id" && $observed_attempt == 3 ]] ||
    fail "Observed source559 is not attempt-0003"
  if [[ $observed_status == running ]]; then
    [[ $queued == "$expected_queued" && $running == 1 && $active == 1 ]] ||
      fail "Unexpected queue/running counts after source559 claim"
  elif [[ $observed_status == succeeded ]]; then
    [[ $queued == "$expected_queued" && $running == 0 && $active == 0 ]] ||
      fail "Unexpected counts after source559 completed early"
  else
    fail "source559 was not running or completed after pause"
  fi
  log "source559 attempt-0003 status=$observed_status; queued=$queued; running=$running; other tasks unchanged"
}

pause_and_snapshot() {
  api_post_discard "/api/batches/$batch_id/pause" "{}"
  if collect_task_pages && task_line=$(task_summary); then
    IFS=$'\t' read -r snapshot_total snapshot_succeeded snapshot_queued snapshot_running snapshot_failed \
      snapshot_interrupted snapshot_cancelled snapshot_id snapshot_status snapshot_attempt snapshot_count \
      snapshot_retryable snapshot_other_bad <<<"$task_line"
    log "post-pause snapshot: total=$snapshot_total succeeded=$snapshot_succeeded queued=$snapshot_queued running=$snapshot_running failed=$snapshot_failed other_bad=$snapshot_other_bad"
  else
    log "post-pause snapshot unavailable; retained all evidence"
  fi
}

rollback_before_retry() {
  phase="rollback before retry"
  [[ -n $env_backup && -f $env_backup ]] || return 1
  rollback_attempted=1
  atomic_copy_file "$env_backup" "$env_file" 1 || return 1
  cmp -s -- "$env_backup" "$env_file" || return 1
  systemctl restart "$worker_service" || return 1
  wait_health_capacity 2 1 || return 1
  env_changed=0
  log "pre-retry rollback restored task_parallelism=2; batch remains paused"
}

on_exit() {
  local rc=$?
  if (( rc != 0 )); then
    local failed_text=$failed_line
    [[ -n $failed_text ]] || failed_text=unknown
    log "FAILED phase=$phase line=$failed_text exit=$rc"
    if (( retry_started )); then
      if pause_best_effort; then
        log "Retry started; evidence retained and batch left paused"
      else
        log "Retry started; evidence retained, but best-effort pause failed; manual pause is required"
      fi
    elif (( env_changed && !rollback_attempted )); then
      if ! rollback_before_retry; then
        log "Pre-retry rollback could not be verified; backup retained at $env_backup; manual intervention required"
      fi
    fi
  fi
  if [[ -n $tmp_dir && -d $tmp_dir ]]; then
    rm -rf -- "$tmp_dir"
  fi
  exit "$rc"
}

prepare_logging() {
  local log_parent log_name
  if (( dry_run )); then
    [[ -z $log_path ]] || { printf '%s\n' '--log is not allowed with --dry-run; dry-run writes stdout only' >&2; exit 2; }
    log_path=stdout
    return
  fi
  if [[ -z $log_path ]]; then
    log_path=/tmp/powercontext-source559-resume-$(date +%Y%m%d-%H%M%S).log
  fi
  log_parent=$(realpath -e "$(dirname -- "$log_path")") || { printf 'Log parent cannot be resolved\n' >&2; exit 2; }
  case $log_parent in
    /tmp|/var/log/powercontext) ;;
    *) printf 'Log parent is outside the safe log roots: %s\n' "$log_parent" >&2; exit 2 ;;
  esac
  log_name=$(basename -- "$log_path")
  [[ $log_name != . && $log_name != .. ]] || { printf 'Invalid log name\n' >&2; exit 2; }
  log_path=$log_parent/$log_name
  [[ ! -L $log_path ]] || { printf 'Refusing symlink log path: %s\n' "$log_path" >&2; exit 2; }
  if [[ -e $log_path ]]; then
    [[ -f $log_path && -w $log_path ]] || { printf 'Log path is not a writable regular file\n' >&2; exit 2; }
  else
    (set -o noclobber; : >"$log_path") || { printf 'Could not safely create log path\n' >&2; exit 2; }
  fi
  chmod 600 -- "$log_path" || { printf 'Could not restrict log permissions\n' >&2; exit 2; }
  exec > >(tee -a "$log_path") 2>&1
}

trap record_error_line ERR
trap on_exit EXIT

while (($# > 0)); do
  case $1 in
    --dry-run) dry_run=1; shift ;;
    --claim-observation)
      (($# == 4)) || { usage >&2; exit 2; }
      claim_observation_test=1
      claim_observation_status=$2
      claim_observation_active=$3
      claim_observation_running=$4
      shift 4
      ;;
    --log)
      (($# >= 2)) || { usage >&2; exit 2; }
      log_path=$2
      shift 2
      ;;
    --help|-h) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
  esac
done

if (( claim_observation_test )); then
  claim_observation_decision \
    "$claim_observation_status" "$claim_observation_active" "$claim_observation_running"
  exit 0
fi

prepare_logging

log "source559 controlled retry starting; dry_run=$dry_run log=$log_path"
[[ $(id -u) == 0 ]] || fail "Operation must run as uid 0"
[[ -d $deploy_dir ]] || fail "Deployment directory is missing"
[[ -f $env_file && ! -L $env_file ]] || fail "Evaluation env file is missing or symlinked"

phase="deployment validation preflight"
current_head=$(cd "$deploy_dir" && git -c "safe.directory=$deploy_dir" rev-parse HEAD 2>/dev/null) ||
  fail "Deployment HEAD could not be read"
[[ $current_head == "$expected_head" ]] || fail "Deployment HEAD is not the audited commit"
log "deployment HEAD verified at audited commit 52e001a"

[[ -f $validation_log && -f $validation_exit ]] || fail "m0 validation evidence files are missing"
for marker in '928 passed' 'backend_rc=0' 'ruff_rc=0' 'format_rc=0' 'ty_rc=0' 'frontend_rc=127' 'npm: command not found'; do
  grep -qF "$marker" "$validation_log" || fail "m0 validation marker is missing: $marker"
done
validation_exit_code=$(tr -d '[:space:]' <"$validation_exit")
[[ $validation_exit_code == 127 ]] || fail "m0 validation exit evidence changed"
log "m0 backend 928/928, ruff, format, and src ty validated; frontend npm absence is a known non-core downgrade (Mac frontend is not claimed as m0)"

systemctl is-active --quiet "$web_service" || fail "Evaluation web service is not active"
systemctl is-active --quiet "$worker_service" || fail "Evaluation worker service is not active"
check_container new-api running
check_container new-api-mysql healthy
check_container new-api-redis healthy
check_data_resources

tmp_dir=$(mktemp -d /tmp/powercontext-source559-resume.XXXXXX)
chmod 700 "$tmp_dir"

phase="API and batch preflight"
api_get_to "/api/batches/$batch_id" "$tmp_dir/batch.json"
batch_summary=$(parse_batch "$tmp_dir/batch.json" 2>/dev/null) || fail "Batch response could not be parsed"
IFS=$'\t' read -r batch_status control_intent pause_reason batch_total <<<"$batch_summary"
[[ $batch_status == paused && $control_intent == pause && $batch_total == "$expected_total" ]] ||
  fail "Batch is not durably paused with total 731"
log "batch paused; pause_reason=$pause_reason; total=$batch_total"

api_get_to "/api/health" "$tmp_dir/health.json"
health_summary=$(parse_health "$tmp_dir/health.json" 2>/dev/null) || fail "Health response could not be parsed"
IFS=$'\t' read -r health_service health_lease health_active health_capacity health_queued health_running <<<"$health_summary"
[[ $health_service == ok && $health_lease == False && $health_active == 0 && $health_capacity == 2 && $health_running == 0 ]] ||
  fail "Worker health is not idle at capacity 2"
log "worker health ok; capacity=$health_capacity; active=$health_active; running=$health_running"

api_get_to "/api/account-usage" "$tmp_dir/usage.json"
used_percent=$(parse_usage "$tmp_dir/usage.json" 2>/dev/null) || fail "Usage response could not be parsed"
python3 - "$used_percent" <<'PY' || fail "Usage is at or above 95%"
import sys
if not 0 <= float(sys.argv[1]) < 95:
    raise SystemExit(1)
PY
log "Codex used_percent=$used_percent (below 95% gate)"

collect_task_pages
task_line=$(task_summary) || fail "Initial task summary failed"
IFS=$'\t' read -r total succeeded queued running failed interrupted cancelled \
  discovered_task_id source_status source_attempt source_count source_retryable other_bad <<<"$task_line"
source559_task_id=$discovered_task_id
[[ -n $source559_task_id ]] || fail "source559 task id was empty"
[[ $total == "$expected_total" && $succeeded == "$expected_succeeded" && $queued == "$expected_queued" &&
   $running == 0 && $failed == 1 && $interrupted == 0 && $cancelled == 0 ]] ||
  fail "Initial counts are not succeeded=560/queued=170/running=0/failed=1"
[[ $source_status == failed && $source_attempt == 2 && $source_count == 2 &&
   $source_retryable == true && $other_bad == 0 ]] ||
  fail "source559 is not the sole retryable failed attempt-0002"
log "source559 attempt-0002 is sole failed item; queued=$queued; running=$running"

current_parallelism=$(read_parallelism 2>/dev/null) || fail "Env parallelism key is unreadable or duplicated"
[[ $current_parallelism == 2 ]] || fail "Env task_parallelism is not exactly 2"
log "evaluation env task_parallelism=2 verified"

if (( dry_run )); then
  log "DRY-RUN: would backup env, change only 2->1, restart only worker, verify capacity=1, retry only source559 attempt-0003, resume, observe one unique claim for <=60s, then pause"
  log "DRY-RUN: pre-retry errors restore env=2 and worker; post-retry errors pause and retain evidence without DB/container cleanup"
  exit 0
fi

phase="worker capacity reduction"
env_backup=$env_file.source559.before.$(date +%Y%m%d-%H%M%S).$$
atomic_copy_file "$env_file" "$env_backup" || fail "Could not create same-directory env backup"
cmp -s -- "$env_backup" "$env_file" || fail "Env backup verification failed"
log "same-directory env backup created at $env_backup (contents suppressed)"
env_changed=1
replace_parallelism 2 1 || fail "Could not change task_parallelism atomically from 2 to 1"
log "only task_parallelism changed atomically 2->1; owner/mode preserved"
systemctl restart "$worker_service" || fail "Evaluation worker restart failed"
wait_health_capacity 1 1 || fail "Worker did not become healthy at capacity=1"
systemctl is-active --quiet "$web_service" || fail "Evaluation web service became inactive"
verify_batch_pause
log "only evaluation worker restarted; health capacity=1 and active/running=0"

phase="source559 retry request"
source559_task_url_id=$(python3 - "$source559_task_id" <<'PY'
from urllib.parse import quote
import sys
print(quote(sys.argv[1], safe=""))
PY
) || fail "Could not encode source559 task id"
idempotency_key=source559-attempt-0003-$(date +%Y%m%d%H%M%S)
retry_started=1
api_post_discard "/api/batches/$batch_id/tasks/$source559_task_url_id/retry" "{\"idempotency_key\":\"$idempotency_key\"}"
api_get_to "/api/batches/$batch_id/tasks/$source559_task_url_id/attempts" "$tmp_dir/attempts.json"
attempt_summary=$(parse_retry "$tmp_dir/attempts.json" 2>/dev/null) || fail "Retry response could not be parsed"
IFS=$'\t' read -r retry_task_id retry_attempt_id retry_attempt_number retry_status <<<"$attempt_summary"
[[ $retry_task_id == "$source559_task_id" && $retry_attempt_number == 3 && $retry_status == queued ]] ||
  fail "Retry did not create queued source559 attempt-0003"
collect_task_pages
task_line=$(task_summary) || fail "Task summary failed after retry request"
IFS=$'\t' read -r total succeeded queued running failed interrupted cancelled \
  retry_observed_id retry_observed_status retry_observed_attempt retry_observed_count retry_observed_retryable other_bad <<<"$task_line"
[[ $total == "$expected_total" && $succeeded == "$expected_succeeded" &&
   $queued == $((expected_queued + 1)) && $running == 0 && $failed == 0 &&
   $interrupted == 0 && $cancelled == 0 && $other_bad == 0 &&
   $retry_observed_id == "$source559_task_id" && $retry_observed_status == queued &&
   $retry_observed_attempt == 3 ]] || fail "Retry changed more than source559"
log "source559 attempt-0003 queued; no other task retried"

phase="controlled resume and unique claim observation"
api_post_discard "/api/batches/$batch_id/resume" "{}"
log "explicit resume sent; observing only source559 for at most 60 seconds"
claimed=0
deadline=$(monotonic_deadline)
while before_monotonic_deadline "$deadline"; do
  api_get_to "/api/batches/$batch_id/tasks/$source559_task_url_id" "$tmp_dir/source559-detail.json"
  source_detail=$(parse_source_detail "$tmp_dir/source559-detail.json" 2>/dev/null) ||
    fail "source559 detail could not be parsed during claim observation"
  IFS=$'\t' read -r observed_id observed_status observed_attempt observed_count <<<"$source_detail"
  [[ $observed_id == "$source559_task_id" && $observed_attempt == 3 ]] ||
    fail "source559 identity or attempt changed during observation"
  if [[ $observed_status == running || $observed_status == succeeded ]]; then
    api_post_discard "/api/batches/$batch_id/pause" "{}"
    claimed=1
    break
  fi
  if [[ $observed_status != queued ]]; then
    pause_and_snapshot
    fail "source559 entered unexpected state during claim observation"
  fi
  api_get_to "/api/health" "$tmp_dir/health.json"
  health_summary=$(parse_health "$tmp_dir/health.json" 2>/dev/null) ||
    fail "Health could not be parsed during claim observation"
  IFS=$'\t' read -r health_service health_lease health_active health_capacity health_queued health_running <<<"$health_summary"
  [[ $health_service == ok && $health_capacity == 1 ]] ||
    fail "Worker health changed during claim observation"
  if [[ $health_active != 0 || $health_running != 0 ]]; then
    # The source detail is authoritative for identity, but it can lag the
    # worker lease by one poll. Re-read the source detail and durable attempt
    # before treating this as an unresolved ownership race.
    api_get_to "/api/batches/$batch_id/tasks/$source559_task_url_id" "$tmp_dir/source559-detail.json"
    source_detail=$(parse_source_detail "$tmp_dir/source559-detail.json" 2>/dev/null) ||
      fail "source559 detail could not be refreshed during claim observation"
    IFS=$'\t' read -r observed_id observed_status observed_attempt observed_count <<<"$source_detail"
    [[ $observed_id == "$source559_task_id" && $observed_attempt == 3 ]] ||
      fail "source559 identity or attempt changed during claim observation"
    if [[ $observed_status == running || $observed_status == succeeded ]]; then
      api_post_discard "/api/batches/$batch_id/pause" "{}"
      claimed=1
      break
    fi
    api_get_to "/api/batches/$batch_id/tasks/$source559_task_url_id/attempts" "$tmp_dir/attempts.json"
    attempt_summary=$(parse_retry "$tmp_dir/attempts.json" 2>/dev/null) ||
      fail "source559 attempts could not be refreshed during claim observation"
    IFS=$'\t' read -r retry_task_id retry_attempt_id retry_attempt_number retry_status <<<"$attempt_summary"
    [[ $retry_task_id == "$source559_task_id" && $retry_attempt_number == 3 ]] ||
      fail "source559 attempt identity changed during claim observation"
    decision=$(claim_observation_decision "$observed_status" "$health_active" "$health_running") ||
      fail "source559 claim observation values were invalid"
    if [[ $decision == reconcile ]]; then
      pause_and_snapshot
      fail "Claim ownership was unresolved before the retry boundary was paused"
    fi
  fi
  sleep 0.5
done
(( claimed == 1 )) || {
  pause_best_effort || true
  fail "source559 was not uniquely claimed within 60 seconds"
}

phase="verify pause after unique claim"
verify_post_claim_state
log "source559 retry boundary is paused; script intentionally does not wait for full attempt"
log "Next: monitor attempt-0003 Gold/OFF/ON/official/report completion; retain this log and all run evidence"
exit 0
