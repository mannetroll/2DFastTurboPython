# PyPI preparation validation — 2026-09-24

The rebuilt Linux and macOS wheels and source archive in `python/dist/` pass the
checks described below. Nothing was uploaded by the agent. The Windows rebuild
is still pending for the complete 0.1.2 set.

## Validated Linux artifacts

- Wheel: `fastturbo-0.1.2-py3-none-manylinux_2_28_x86_64.whl`, native build at
  18:19 with README metadata refreshed afterward, 53,090,964 bytes, SHA256
  `b5fdfe974ecde26aff37cc64bb46eef720050aa2875467d05cd572ae17b75c5b`.
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

## Updated macOS artifact

The macOS wheel built at 18:28 passes the structural and strict Twine checks.
After the README metadata refresh, its SHA256 is
`62db5540e678bcad33ff4f87af048651e7e22a0127ecc93ace160c3e1a6ad10a`.
All shared Python modules and all 152 native build inputs match the sdist,
excluding Finder's `.DS_Store` metadata. The wheel includes the native dylib and
Metal library, targeting arm64 macOS 15 with Swift 6.2.4. Runtime execution on a
Mac has not been verified by this Linux agent.

## PyPI README refresh

The README now describes macOS, Windows and Linux manylinux wheels, CUDA 13.1,
desktop runtime requirements and the Linux source fallback. All three local
wheels have the updated description in METADATA and regenerated RECORD checksums.
Every other wheel entry, including Python code and native binaries, was verified
byte-for-byte unchanged. The sdist was rebuilt with the same README. All four
descriptions match `python/README.md`, and all four strict Twine checks pass.
The public PyPI page changes when the release artifacts are uploaded.

## Remaining Windows artifact

The Windows wheel in `python/dist/` still contains the September 19 build, with
its README metadata refreshed. It passes
structural and metadata checks but contains older Python modules and fails source
consistency, including missing start-spectrum support. The user is rebuilding it
from current source.

Replace and runtime-test the Windows wheel, then run the complete release-set
check and Twine checks on the intended four files. The generic local Linux wheel
has been removed from `python/dist/`; it must remain excluded from PyPI uploads.

PyPI's public JSON API reported latest version 0.1.1 and no 0.1.2 artifacts at the
time of this check. The planned 0.1.2 version can be retained; verify filename
availability again immediately before uploading.
