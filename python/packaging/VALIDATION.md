# PyPI preparation validation — 2026-09-24

The rebuilt Linux manylinux wheel and source archive in `python/dist/` pass the
checks described below. Nothing was uploaded by the agent. The user is rebuilding
the macOS and Windows wheels from the current source for the complete 0.1.2 set.

## Validated Linux artifacts

- Wheel: `fastturbo-0.1.2-py3-none-manylinux_2_28_x86_64.whl`, built at 18:19,
  53,091,003 bytes, SHA256
  `163fe293fad1fcf8eaed9bcf374f322d6dcdb3e9220de70b6a7131887e348da3`.
- Source archive: `fastturbo-0.1.2.tar.gz`. All packaged Python modules and all
  90 native build inputs recorded in the wheel match this source archive.
- The wheel uses CUDA 13.1.115 with SM 80/86/89/90/100/120 machine code and SM 80
  PTX. CUDA runtime, NVRTC and its builtins are bundled; the NVIDIA driver is not.
- Strict Twine metadata checks and the structural artifact checker pass.
  Auditwheel's symbol analysis reports manylinux_2_27 compatibility, satisfying
  the selected manylinux_2_28 tag.

Runtime verification on native Ubuntu x86-64, Python 3.14.4, RTX 3090 and NVIDIA
595.91.07 passes:

- ETDRK4 and CNAB2 each execute 17 finite timesteps, both normally and with forced
  PTX execution. Loaded-library mappings confirm that CUDA runtime, NVRTC and
  builtins come from the extracted wheel with toolkit environment variables cleared.
- The desktop check passes with 77 presentations and a valid 2,606,284-byte PNG.
- The renderer links system libGL.so.1. No private GLX/OpenGL dispatch libraries
  are bundled. The prior invalid-context failure is fixed in this artifact.

The toolkit is still installed on this validation host. Loaded paths were
verified, but a completely toolkit-free machine and the oldest supported OS /
PySide6 combination have not been exercised here.

## Source fallback and packaging checks

A self-contained sdist builds and can be repacked outside the repository. Missing
native reference sources are rejected. The native core_lifecycle test passes
from an isolated source build. Local builds with bundling disabled exclude CUDA
runtime libraries and use MIT metadata; installation and both CLI help commands
pass. The source archive passes the contents checker and strict Twine checks.

The Dockerfile stages the CUDA runtime RPM's own license from
`/usr/share/licenses/cuda-cudart-13-1/LICENSE` at the toolkit-root EULA path. The
entrypoint checks for the license before compiling.

The release checker now compares every wheel's Python modules with the sdist
when `--release-set` is used. Matching Windows CRLF line endings are accepted;
stale, missing and extra modules are rejected. This prevents a mixed-source
release from passing solely because filenames all say 0.1.2.

## Remaining cross-platform artifacts

The macOS and Windows wheels currently in `python/dist/` were built on September
19 and contain older app, controls, engine, main and build modules. They pass
structural and metadata checks but fail source consistency, including missing
start-spectrum support. The user is rebuilding both from current source.

Replace and runtime-test those two wheels, then run the complete release-set
check and Twine checks on the intended four files. Exclude the generic
`fastturbo-0.1.2-py3-none-linux_x86_64.whl` from PyPI uploads.

PyPI's public JSON API reported latest version 0.1.1 and no 0.1.2 artifacts at the
time of this check. The planned 0.1.2 version can be retained; verify filename
availability again immediately before uploading.
