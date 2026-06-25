#!/bin/bash
# Backwards-compatible entry point for a clean frontend documentation build.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$SCRIPT_DIR/deploy-frontend.sh" "$@"
