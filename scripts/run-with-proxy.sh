#!/bin/sh

set -eu

proxy_url="${POLYMARKET_PROXY_URL:-http://127.0.0.1:7897}"
local_no_proxy="localhost,127.0.0.1,::1"

export HTTP_PROXY="$proxy_url"
export HTTPS_PROXY="$proxy_url"
export ALL_PROXY="$proxy_url"
export http_proxy="$proxy_url"
export https_proxy="$proxy_url"
export all_proxy="$proxy_url"

if [ -n "${NO_PROXY:-}" ]; then
  export NO_PROXY="$local_no_proxy,$NO_PROXY"
else
  export NO_PROXY="$local_no_proxy"
fi
export no_proxy="$NO_PROXY"

mode="${1:-dev}"

echo "Using proxy: $proxy_url"

case "$mode" in
  dev)
    exec npm run dev:all
    ;;
  start)
    exec npm run start:local
    ;;
  *)
    echo "Usage: $0 [dev|start]" >&2
    exit 2
    ;;
esac
