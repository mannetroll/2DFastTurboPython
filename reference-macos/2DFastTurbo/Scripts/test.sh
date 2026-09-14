#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 Scripts/generate_project.py
xcodebuild -project 2DFastTurbo.xcodeproj -scheme 2DFastTurboBench \
  -configuration Release -destination 'platform=macOS,arch=arm64' \
  -derivedDataPath "${FAST_TURBO_DERIVED_DATA:-/tmp/2DFastTurboDerived}" \
  ARCHS=arm64 ONLY_ACTIVE_ARCH=YES test "$@"
