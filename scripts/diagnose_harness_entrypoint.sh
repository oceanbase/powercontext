#!/usr/bin/env bash
# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

set -euo pipefail

[[ ${GITHUB_RUN_ID:?} =~ ^[0-9]+$ ]]
[[ ${GITHUB_RUN_ATTEMPT:?} =~ ^[0-9]+$ ]]
diag_dir=$(mktemp -d "${RUNNER_TEMP:?}/harness-entrypoint.XXXXXXXX")
image_tag="pc-harness-entrypoint-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}:diagnostic"
cache_id="pc-harness-entrypoint-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}"
container_id=
image_id=
cleanup() {
    status=$?
    trap - EXIT
    if [[ -n "$container_id" ]]; then
        docker rm --force "$container_id" || true
    fi
    if [[ -n "$image_id" ]] && [[ $(docker image inspect --format '{{.Id}}' "$image_tag" 2>/dev/null || true) == "$image_id" ]]; then
        docker image rm "$image_tag" || true
    fi
    rm -rf "$diag_dir"
    exit "$status"
}
trap cleanup EXIT

if docker image inspect "$image_tag" >/dev/null 2>&1; then
    echo "Refusing to reuse an existing diagnostic image tag." >&2
    exit 1
fi
export TMPDIR="$diag_dir/tmp" UV_CACHE_DIR="$diag_dir/uv-cache" UV_PYTHON_INSTALL_DIR="$diag_dir/python"
mkdir -p "$TMPDIR" "$UV_CACHE_DIR" "$UV_PYTHON_INSTALL_DIR"
version=$(uvx --from hatchling --with hatch-vcs hatchling version)
test -n "$version"

# Only the BuildKit cache ID differs from the checked-in Dockerfile.
python3 - "$cache_id" "$diag_dir/Dockerfile" <<'PY'
from pathlib import Path
import sys

source = Path("e2e/bub/Dockerfile").read_text()
old = "id=powercontext-bub-e2e-uv"
assert source.count(old) == 1
Path(sys.argv[2]).write_text(source.replace(old, "id=" + sys.argv[1]))
PY
docker build --no-cache --tag "$image_tag" --file "$diag_dir/Dockerfile" \
    --build-arg "POWERCONTEXT_VERSION=$version" .
image_id=$(docker image inspect --format '{{.Id}}' "$image_tag")
echo "Diagnostic image ID: $image_id"
docker image inspect "$image_tag" | python3 -c '
import json, sys
image = json.load(sys.stdin)[0]
config = image["Config"]
print(json.dumps({"Entrypoint": config.get("Entrypoint"), "Cmd": config.get("Cmd"),
    "User": config.get("User"), "WorkingDir": config.get("WorkingDir"),
    "PATH": next((v[5:] for v in config.get("Env", []) if v.startswith("PATH=")), None)}, indent=2))
'
entrypoint=$(docker image inspect --format '{{json .Config.Entrypoint}}' "$image_tag" |
    python3 -c 'import json,sys; v=json.load(sys.stdin); assert len(v)==1 and v[0].startswith("/"); print(v[0])')

probe=$(cat <<'SH'
set +e
id
printf 'PATH=%s\n' "$PATH"
for path in / /usr /usr/local /usr/local/bin /bin /bin/sh "$DIAG_ENTRYPOINT"; do
    ls -ld "$path"
    stat -c '%a %A %u:%g %n' "$path"
    if command -v getfacl >/dev/null 2>&1; then getfacl -p "$path"; fi
done
if ! command -v getfacl >/dev/null 2>&1; then echo 'getfacl unavailable in image'; fi
printf 'entrypoint executable: '; test -x "$DIAG_ENTRYPOINT" && echo yes || echo no
printf 'shebang: '; head -n 1 "$DIAG_ENTRYPOINT"
printf 'shell target: '; readlink -f /bin/sh
echo 'Relevant mount information:'
awk '$5 == "/" || $5 == "/usr" || $5 == "/usr/local" || $5 == "/usr/local/bin" || $5 == "/bin" { print $5, $6, $7, $8, $9, $10 }' /proc/self/mountinfo
SH
)
# Match the harness's privileged setting, but attach no host bind mounts or network.
container_id=$(docker create --privileged --network none --entrypoint /bin/sh \
    --env "DIAG_ENTRYPOINT=$entrypoint" "$image_tag" -c "$probe")
echo "Diagnostic container ID: $container_id"
docker inspect "$container_id" | python3 -c '
import json,sys
c=json.load(sys.stdin)[0]
print(json.dumps({"Mounts":[{k:m.get(k) for k in ("Type","Destination","RW","Propagation")} for m in c["Mounts"]],
    "Privileged":c["HostConfig"]["Privileged"], "NetworkMode":c["HostConfig"]["NetworkMode"],
    "SecurityOpt":c["HostConfig"]["SecurityOpt"]}, indent=2))
'
# Copy from this stopped container even if executing /bin/sh is denied.
if docker cp "$container_id:$entrypoint" "$diag_dir/entrypoint"; then
    ls -l "$diag_dir/entrypoint"
    head -n 1 "$diag_dir/entrypoint"
fi
docker start --attach "$container_id"
