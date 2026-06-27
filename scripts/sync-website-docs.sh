#!/bin/bash
# Sync source documentation into the Docusaurus website docs directory.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SOURCE_DOCS_DIR="$PROJECT_ROOT/docs"
WEBSITE_DOCS_DIR="$PROJECT_ROOT/docs/website/docs"

echo "  Cleaning docs/website/docs directory..."
mkdir -p "$WEBSITE_DOCS_DIR"
find "$WEBSITE_DOCS_DIR" -mindepth 1 -maxdepth 1 ! -name '.git' -exec rm -rf {} + 2>/dev/null || true

echo "  Copying documentation files from docs/ to docs/website/docs/..."
for dirname in "$SOURCE_DOCS_DIR"/*/; do
    [ -d "$dirname" ] || continue
    name="$(basename "$dirname")"

    case "$name" in
        website|images)
            continue
            ;;
    esac

    echo "    Copying directory: $name"
    cp -R "$dirname" "$WEBSITE_DOCS_DIR/"
done

echo "  Copied documentation directories:"
find "$WEBSITE_DOCS_DIR" -maxdepth 1 -mindepth 1 -type d -print | sort
