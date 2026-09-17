#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
export UV_CACHE_DIR="${UV_CACHE_DIR:-$PWD/.uv-cache}"
uv run fastturbo-build
ctest --test-dir build/linux --output-on-failure
# The default gate also runs on compute-only hosts. --gui includes the actual
# desktop/control, GPU pixel-parity and autonomous renderer checks.
if [[ "${1:-}" == "--gui" ]]; then
    shift
    uv run pytest -q "$@"
else
    uv run pytest -q tests/test_numerics.py tests/test_linux.py -m 'not gui' "$@"
fi
