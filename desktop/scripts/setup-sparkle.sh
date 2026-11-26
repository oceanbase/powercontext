#!/bin/bash
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

VERSION="2.9.6"
EXPECTED_SHA256="52bf9e88cdd972fc0c81501377a880e90d47031bd8ca5462488f843e2609e192"
DESTINATION="$(cd "$(dirname "$0")/../src-tauri" && pwd)"

if [ -d "$DESTINATION/Sparkle.framework" ] \
    && [ -x "$DESTINATION/sparkle-bin/sign_update" ] \
    && [ -f "$DESTINATION/sparkle-bin/.archive-sha256" ] \
    && [ "$(<"$DESTINATION/sparkle-bin/.archive-sha256")" = "$EXPECTED_SHA256" ]; then
    echo "Sparkle ${VERSION} is already installed"
    exit 0
fi

TEMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TEMP_DIR"' EXIT

curl --fail --location --retry 3 -o "$TEMP_DIR/sparkle.tar.xz" \
    "https://github.com/sparkle-project/Sparkle/releases/download/${VERSION}/Sparkle-${VERSION}.tar.xz"
printf '%s  %s\n' "$EXPECTED_SHA256" "$TEMP_DIR/sparkle.tar.xz" | shasum -a 256 -c -
tar -xf "$TEMP_DIR/sparkle.tar.xz" -C "$TEMP_DIR"
test -d "$TEMP_DIR/Sparkle.framework"
test -x "$TEMP_DIR/bin/sign_update"

cp -R "$TEMP_DIR/Sparkle.framework" "$DESTINATION/Sparkle.framework"
cp -R "$TEMP_DIR/bin" "$DESTINATION/sparkle-bin"
printf '%s\n' "$EXPECTED_SHA256" > "$DESTINATION/sparkle-bin/.archive-sha256"

echo "Sparkle ${VERSION} installed and verified"
