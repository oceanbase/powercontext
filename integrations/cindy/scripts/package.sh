#!/usr/bin/env bash
# Copyright (c) 2026 OceanBase. Licensed under the Apache License, Version 2.0.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
plugin_root="$repo_root/integrations/cindy/plugins/powercontext"
plugin_version="$(node -p "require(process.argv[1]).version" "$plugin_root/ghost.json")"
output="$repo_root/dist/powercontext-cindy-$plugin_version.cindy"
package_temp="$(mktemp -d)"
trap 'rm -rf "$package_temp"' EXIT

# Build a new archive every time, so removed files cannot survive from an earlier build.
cd "$plugin_root"
zip -q -X "$package_temp/plugin.cindy" \
  ghost.json main.js MANUAL.md settings.html settings.js node/worker.cjs \
  locales/en.json locales/zh-CN.json
cd "$repo_root"
zip -q -X "$package_temp/plugin.cindy" LICENSE
mkdir -p "$repo_root/dist"
mv "$package_temp/plugin.cindy" "$output"
printf '%s\n' "$output"
