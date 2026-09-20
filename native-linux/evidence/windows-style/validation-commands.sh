#!/bin/bash
set -euo pipefail
cd /home/tobbe/github/2DFastTurboPython
EVIDENCE=native-linux/evidence/windows-style
cmake -S . -B build-linux -DCMAKE_BUILD_TYPE=Release -DCMAKE_CUDA_ARCHITECTURES=86 -DFASTTURBO_NATIVE_GUI_TESTS=ON > "$EVIDENCE/configure.log" 2>&1
cmake --build build-linux -j2 > "$EVIDENCE/build.log" 2>&1
ctest --test-dir build-linux --output-on-failure --output-junit /home/tobbe/github/2DFastTurboPython/native-linux/evidence/windows-style/ctest.xml > "$EVIDENCE/ctest.log" 2>&1
./fastturbo-linux --self-test --output "$EVIDENCE/desktop-test.json" --capture "$EVIDENCE/desktop.png" > "$EVIDENCE/desktop-test.log" 2>&1
env FASTTURBO_CUDA_ARCH=86 FASTTURBO_LINUX_RENDERER=ON FASTTURBO_EVIDENCE_DIR=/home/tobbe/github/2DFastTurboPython/native-linux/evidence/windows-style/python FASTTURBO_REFERENCE_EVIDENCE=/home/tobbe/github/2DFastTurboPython/native-linux/evidence/windows-style/reference QT_QPA_PLATFORM=xcb python/.venv/bin/python -m pytest -q -ra python/tests reference-linux/tests/test_reference.py --junitxml=native-linux/evidence/windows-style/pytest.xml > "$EVIDENCE/pytest.log" 2>&1
env -u DISPLAY -u WAYLAND_DISPLAY uv run --project python 2dbench --seconds 10 --json > "$EVIDENCE/2dbench.json" 2> "$EVIDENCE/2dbench.log"
python3 native-linux/scripts/compare.py --output "$EVIDENCE/after" --runs 3 --seconds 10 --sizes 2048 4096 > "$EVIDENCE/after.log" 2>&1
ldd build-linux/fastturbo-linux > "$EVIDENCE/ldd.txt"
