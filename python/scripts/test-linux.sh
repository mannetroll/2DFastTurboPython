#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
export UV_CACHE_DIR="${UV_CACHE_DIR:-$PWD/.uv-cache}"
uv run fastturbo-build
ctest --test-dir build/linux --output-on-failure
# The named headless gate does not claim the blocked GUI/rendering tests pass.
# Run `uv run pytest -q` separately to see the complete application's status.
uv run pytest -q tests/test_numerics.py tests/test_linux.py -k 'not rendering' "$@"
