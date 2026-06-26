#!/bin/bash
# Sync source documentation into the Docusaurus website docs directory.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SOURCE_DOCS_DIR="$PROJECT_ROOT/docs"
WEBSITE_DOCS_DIR="$PROJECT_ROOT/docs/website/docs"

if [ ! -d "$SOURCE_DOCS_DIR" ]; then
    echo "Error: source docs directory not found: $SOURCE_DOCS_DIR" >&2
    exit 1
fi

echo "  Cleaning docs/website/docs directory..."
mkdir -p "$WEBSITE_DOCS_DIR"
find "$WEBSITE_DOCS_DIR" -mindepth 1 -maxdepth 1 ! -name '.git' -exec rm -rf {} + 2>/dev/null || true

echo "  Copying documentation files from docs/ to docs/website/docs/..."
synced_count=0
shopt -s nullglob
for dirname in "$SOURCE_DOCS_DIR"/*/; do
    name="$(basename "$dirname")"

    case "$name" in
        website|images)
            continue
            ;;
    esac

    echo "    Copying directory: $name"
    cp -R "${dirname%/}" "$WEBSITE_DOCS_DIR/"
    synced_count=$((synced_count + 1))
done

echo "  Verifying required documentation directories..."
for dirname in "$SOURCE_DOCS_DIR"/*/; do
    name="$(basename "$dirname")"

    case "$name" in
        website|images)
            continue
            ;;
    esac

    if [ ! -d "$WEBSITE_DOCS_DIR/$name" ]; then
        echo "Error: expected directory missing after sync: $WEBSITE_DOCS_DIR/$name" >&2
        exit 1
    fi
done
shopt -u nullglob

if [ "$synced_count" -eq 0 ]; then
    echo "Error: no documentation directories were synced from $SOURCE_DOCS_DIR" >&2
    exit 1
fi

echo "  Copied documentation directories:"
find "$WEBSITE_DOCS_DIR" -maxdepth 1 -mindepth 1 -type d -print | sort
echo "  ✓ synced $synced_count documentation directories"
