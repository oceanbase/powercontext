#!/bin/sh
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

set -eu

docker_log=/tmp/powercontext-e2e-dockerd.log
storage_driver=${POWERCONTEXT_E2E_DOCKER_STORAGE_DRIVER:-vfs}
server_address=${POWERCONTEXT_E2E_SERVER_ADDRESS:-powercontext:8000}

is_loopback_proxy() {
    case "${1:-}" in
        http://127.0.0.1:* | https://127.0.0.1:* | http://localhost:* | https://localhost:*) return 0 ;;
        *) return 1 ;;
    esac
}

# A host-local proxy cannot be reached from the nested Docker daemon.
if is_loopback_proxy "${HTTP_PROXY:-}"; then unset HTTP_PROXY; fi
if is_loopback_proxy "${HTTPS_PROXY:-}"; then unset HTTPS_PROXY; fi
if is_loopback_proxy "${ALL_PROXY:-}"; then unset ALL_PROXY; fi
if is_loopback_proxy "${http_proxy:-}"; then unset http_proxy; fi
if is_loopback_proxy "${https_proxy:-}"; then unset https_proxy; fi
if is_loopback_proxy "${all_proxy:-}"; then unset all_proxy; fi

dockerd \
    --host=unix:///var/run/docker.sock \
    --storage-driver="$storage_driver" \
    --log-level=error \
    >"$docker_log" 2>&1 &

attempt=0
until docker info >/dev/null 2>&1; do
    attempt=$((attempt + 1))
    if [ "$attempt" -ge 60 ]; then
        echo "The nested Docker daemon did not become ready." >&2
        sed -n '1,160p' "$docker_log" >&2
        exit 1
    fi
    sleep 1
done

socat TCP-LISTEN:8000,bind=0.0.0.0,fork,reuseaddr "TCP:$server_address" &

exec powercontext-e2e "$@"
