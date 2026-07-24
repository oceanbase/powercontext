#!/usr/bin/env sh
set -eu

vec1_source_url="https://sqlite.org/vec1/raw/vec1.c?ci=version-0.7"
vec1_source_sha256="8571bb4f77f9547d11ad11e2f72e0de7d3b2ab44e7930151998bce9377ed4b86"
sqlite_headers_url="https://sqlite.org/2025/sqlite-amalgamation-3500400.zip"
sqlite_headers_sha256="1d3049dd0f830a025a53105fc79fd2ab9431aea99e137809d064d8ee8356b032"

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
install_dir=${POWERCONTEXT_VEC1_DIR:-"$project_root/.powercontext"}
extension_path="$install_dir/vec1.so"

if [ "$(uname -s)" != "Linux" ]; then
    echo "install_vec1.sh currently supports Linux only." >&2
    exit 1
fi

for required_command in cc curl install sha256sum unzip uv; do
    if ! command -v "$required_command" >/dev/null 2>&1; then
        echo "Required command not found: $required_command" >&2
        exit 1
    fi
done

build_dir=$(mktemp -d "${TMPDIR:-/tmp}/powercontext-vec1.XXXXXX")
trap 'rm -rf -- "$build_dir"' EXIT HUP INT TERM

curl -fsSL "$vec1_source_url" -o "$build_dir/vec1.c"
curl -fsSL "$sqlite_headers_url" -o "$build_dir/sqlite-amalgamation.zip"

printf '%s  %s\n' "$vec1_source_sha256" "$build_dir/vec1.c" | sha256sum -c -
printf '%s  %s\n' "$sqlite_headers_sha256" "$build_dir/sqlite-amalgamation.zip" | sha256sum -c -

unzip -q "$build_dir/sqlite-amalgamation.zip" -d "$build_dir"
cc \
    -O3 \
    -DNDEBUG \
    -fPIC \
    -shared \
    -I"$build_dir/sqlite-amalgamation-3500400" \
    "$build_dir/vec1.c" \
    -o "$build_dir/vec1.so"

mkdir -p "$install_dir"
install -m 0644 "$build_dir/vec1.so" "$extension_path"

POWERCONTEXT_VEC1_EXTENSION="$extension_path" uv run python - <<'PY'
import os

import apsw

connection = apsw.Connection(":memory:")
try:
    connection.enable_load_extension(True)
    connection.load_extension(os.environ["POWERCONTEXT_VEC1_EXTENSION"])
    connection.enable_load_extension(False)
    version = connection.execute("SELECT vec1_info()").fetchone()
    if version is None:
        raise RuntimeError("Vec1 did not return version information")
    print(f"Vec1 ready: {version[0]}")
finally:
    connection.close()
PY

printf 'Extension path: %s\n' "$extension_path"
