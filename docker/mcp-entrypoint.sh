#!/usr/bin/env sh
set -eu

cd /app

export MCP_HOST="${MCP_HOST:-0.0.0.0}"
export MCP_PORT="${MCP_PORT:-8765}"

exec python mcp_server/server.py
