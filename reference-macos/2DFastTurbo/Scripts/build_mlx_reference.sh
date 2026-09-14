#!/bin/bash
# Optional predecessor benchmark: does not alter or link MLX into the new app.
set -euo pipefail
cd "$(dirname "$0")/.."
astra_products="${ASTRA_PRODUCTS:-/tmp/MLXAstraDerived/Build/Products/Release}"
astra_packages="${ASTRA_PACKAGES:-../MLXAstra/.build/SourcePackages/checkouts}"
reference_output="${ASTRA_REFERENCE_OUTPUT:-/tmp/2DFastTurboAstraReference}"
if [[ ! -d "$astra_products/MLX.swiftmodule" || ! -d "$astra_packages/mlx-swift/Source/Cmlx/include" ]]; then
  echo "Build MLXAstra Release first, or set ASTRA_PRODUCTS and ASTRA_PACKAGES to its existing build/checkouts." >&2
  exit 1
fi
mkdir -p "$reference_output"
framework_flags=()
for framework in "$astra_products"/PackageFrameworks/*.framework; do
  framework_name="$(basename "$framework" .framework)"
  framework_flags+=(-framework "$framework_name")
done
xcrun swiftc -O -whole-module-optimization -swift-version 5 \
  -target arm64-apple-macos15.0 -module-name LegacyAstraBench \
  -module-cache-path "$reference_output/ModuleCache" \
  -I "$astra_products" \
  -I "$astra_packages/mlx-swift/Source/Cmlx/include" \
  -I "$astra_packages/swift-numerics/Sources/_NumericsShims/include" \
  -F "$astra_products/PackageFrameworks" \
  -Xlinker -rpath -Xlinker "$astra_products/PackageFrameworks" \
  -framework AppKit -framework Metal "${framework_flags[@]}" \
  Sources/FastTurboCore/FastTurboTypes.swift \
  Benchmarks/LegacyAstra/SimulationTypes.swift \
  Benchmarks/LegacyAstra/MLXTurbulenceSolver.swift \
  Benchmarks/LegacyAstra/FastTurboCore.swift \
  Benchmarks/LegacyAstra/BenchmarkCLI.swift \
  Sources/FastTurboBench/BenchMain.swift \
  -o "$reference_output/LegacyAstraBench"
if [[ -d "$astra_products/mlx-swift_Cmlx.bundle" ]]; then
  ditto "$astra_products/mlx-swift_Cmlx.bundle" "$reference_output/mlx-swift_Cmlx.bundle"
fi
echo "$reference_output/LegacyAstraBench"
