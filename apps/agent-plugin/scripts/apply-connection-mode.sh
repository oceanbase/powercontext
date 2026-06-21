#!/usr/bin/env bash
# Copy the chosen PowerMem connection template to .mcp.json (plugin root).
# Usage: bash scripts/apply-connection-mode.sh mcp|http
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
case "${1:-}" in
  mcp)
    cp "${ROOT}/config/mcp-mode.mcp.json" "${ROOT}/.mcp.json"
    echo "Applied MCP mode -> ${ROOT}/.mcp.json"
    ;;
  http)
    cp "${ROOT}/config/http-mode.mcp.json" "${ROOT}/.mcp.json"
    echo "Applied HTTP-only mode (no PowerMem MCP tools) -> ${ROOT}/.mcp.json"
    ;;
  *)
    echo "usage: $0 mcp|http" >&2
    exit 1
    ;;
esac
