#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
export POLYCOPY_COLLECTION_CONFIG="${POLYCOPY_COLLECTION_CONFIG:-collection.toml}"
exec uv run uvicorn backend.collection_server:configured_app --factory --host 0.0.0.0 --port 8731 --workers 1
