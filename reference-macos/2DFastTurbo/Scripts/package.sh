#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."

# A self-contained, locally signed application; no installed MLX frameworks or
# Xcode package cache are needed when running the resulting bundle.
FAST_TURBO_SCHEME=2DFastTurbo bash Scripts/build.sh "$@"
products="${FAST_TURBO_DERIVED_DATA:-/tmp/2DFastTurboDerived}/Build/Products/Release"
mkdir -p Build
ditto "$products/2DFastTurbo.app" Build/2DFastTurbo.app
codesign --verify --deep --strict Build/2DFastTurbo.app
echo "Ready: $PWD/Build/2DFastTurbo.app"
