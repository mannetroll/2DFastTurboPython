#!/usr/bin/env bash
set -euo pipefail
version=${1:?Usage: build-native-linux.sh VERSION}
cmake -S /source -B /build -G Ninja -DCMAKE_BUILD_TYPE=Release \
  -DFASTTURBO_DISTRIBUTION=ON -DFASTTURBO_BUILD_NATIVE_GUI=ON \
  '-DCMAKE_CUDA_ARCHITECTURES=80-real;86-real;89-real;90-real;100-real;120-real;80-virtual'
cmake --build /build --target fastturbo-linux 2dbench native_controller_tests --parallel 2
ctest --test-dir /build -R '^native_gui_cli$' --output-on-failure
/build/2dbench --help
exe=/build/fastturbo-linux
"$exe" --licenses > /build/licenses.txt
if readelf -d "$exe" | grep -E 'NEEDED.*(libcudart|libnvrtc|libnvptx|libstdc\+\+|libgcc_s|libpng|libz\.so|libpython|libQt|libcuda\.so)'; then
  echo 'Native distribution contains an unexpected runtime dependency.' >&2
  exit 1
fi
install -m 755 "$exe" "/output/2DFastTurbo-${version}-Linux-x86_64"
tar -czf "/output/2DFastTurbo-${version}-Linux-x86_64.tar.gz" -C /output "2DFastTurbo-${version}-Linux-x86_64"
# Keep matching headless validation executables with the build evidence.
install -m 755 /build/2dbench /build/native-linux/native_controller_tests /output/
