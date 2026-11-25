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

# Build and validate the publishable Linux candidate in the same glibc baseline.
set -euo pipefail

docker run --rm -i \
  --user "$(id -u):$(id -g)" \
  --env HOME="$RUNNER_TEMP/portable-home" \
  --volume "$GITHUB_WORKSPACE:$GITHUB_WORKSPACE" \
  --volume "$RUNNER_TEMP:$RUNNER_TEMP" \
  --workdir "$GITHUB_WORKSPACE" \
  --env GITHUB_WORKSPACE --env RUNNER_TEMP --env UPGRADE_GATE_ROOT \
  --env GITHUB_OUTPUT --env GITHUB_SHA --env PC_UPGRADE_SOURCE_SHA --env GITHUB_RUN_ID \
  --env GITHUB_ACTOR --env GITHUB_STEP_SUMMARY \
  --env RELEASE --env SELECTION --env RELEASE_TAG \
  --env PC_CI_PYTHON_INSTALLER --env PC_CI_PYTHON_MIRROR \
  --env PYTHONPATH="$UPGRADE_GATE_ROOT/scripts" \
  --env CARGO_HOME="$RUNNER_TEMP/portable-cargo" \
  --env RUSTUP_HOME="$RUNNER_TEMP/portable-rustup" \
  --env UV_CACHE_DIR="$RUNNER_TEMP/portable-uv-cache" \
  --env RUSTUP_TOOLCHAIN=1.95.0 \
  quay.io/pypa/manylinux_2_28_x86_64 \
  bash -euc '
    mkdir -p "$HOME"
    /opt/python/cp311-cp311/bin/python -m venv "$RUNNER_TEMP/portable-tools"
    export PATH="$RUNNER_TEMP/portable-tools/bin:$CARGO_HOME/bin:$PATH"
    python -m pip install --disable-pip-version-check uv==0.10.12 packaging==26.3 >&2
    if ! rustup run 1.95.0 rustc --version >/dev/null 2>&1; then
      curl --proto "=https" --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal --default-toolchain 1.95.0 >&2
    fi
    git config --global --add safe.directory "$GITHUB_WORKSPACE"
    exec "$@"
  ' portable-upgrade "$@"
