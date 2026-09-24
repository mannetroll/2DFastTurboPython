#!/usr/bin/env bash
# Called inside the manylinux image with a read-only sdist and output mount.
set -euo pipefail
if [[ $# != 2 ]]; then
    echo 'Usage: build-manylinux.sh /input/fastturbo-VERSION.tar.gz /output' >&2
    exit 2
fi
sdist=$(realpath "$1")
output=$(realpath "$2")
work=$(mktemp -d)
tar -xzf "$sdist" -C "$work"
cd "$work"/fastturbo-*
export FASTTURBO_BUNDLE_CUDA=ON
export FASTTURBO_LINUX_RENDERER=ON
# One CPU platform wheel with CUDA cubins and forward-compatible PTX.
# Include Ampere datacenter/desktop, Ada, Hopper and Blackwell targets.
export FASTTURBO_CUDA_ARCHITECTURES='80-real;86-real;89-real;90-real;100-real;120-real;80-virtual'
unset FASTTURBO_CUDA_ARCH
# Include the distro notices for libraries auditwheel may graft into the wheel.
mkdir -p src/fastturbo/_native
python - <<'PY'
from pathlib import Path
import subprocess
root = Path('/usr/share/licenses')
packages = ('libpng', 'libglvnd', 'libglvnd-glx', 'libglvnd-opengl', 'libxcb', 'libXau')
parts = []
for package in packages:
    result = subprocess.run(['rpm', '-ql', package], text=True, capture_output=True, check=True)
    for line in result.stdout.splitlines():
        path = Path(line)
        if path.is_relative_to(root) and path.is_file():
            parts.append(f'{package}: {path.name}\n\n{path.read_text()}\n')
if not parts:
    raise SystemExit('Missing manylinux dependency licenses')
Path('src/fastturbo/_native/Linux-Libraries-LICENSE.txt').write_text('\n'.join(parts))
PY
python -m build --wheel --outdir "$work/raw"
auditwheel show "$work"/raw/*.whl
# Never exclude libcuda or force a tag: the engine dlopens the host driver.
auditwheel repair --plat manylinux_2_28_x86_64 --only-plat -w "$work/repaired" "$work"/raw/*.whl
auditwheel show "$work"/repaired/*.whl
python scripts/check_distributions.py "$work"/repaired/*.whl
python -m twine check --strict "$work"/repaired/*.whl
# No GPU or compiler is needed for loading the shared library and CLI help.
python -m venv "$work/installed"
"$work/installed/bin/pip" install --no-deps "$work"/repaired/*.whl
cd /tmp
"$work/installed/bin/2dbench" --help
"$work/installed/bin/2dfastturbo" --help
"$work/installed/bin/python" - <<'PY'
import ctypes
from pathlib import Path
import fastturbo
ctypes.CDLL(str(Path(fastturbo.__file__).parent / '_native/libfastturbo.so'))
PY
cp "$work"/repaired/*.whl "$output/"
