# PyPI preparation validation — 2026-09-24

No artifacts have been uploaded. Version remains 0.1.2. The current manylinux
wheel needs rebuilding with the OpenGL fix described below before release.

## Container-built CUDA 13.1 wheel

Docker is installed and the user successfully built
`fastturbo-0.1.2-py3-none-manylinux_2_28_x86_64.whl` at 18:06. This first artifact
is 53,359,210 bytes with SHA256
`c79f8d081f4027207402c5eb24d0fc5311d4cc4aa2cdb4039ac917798c59dd32`.

Validated on Ubuntu x86-64, Python 3.14.4, RTX 3090 and NVIDIA driver 595.91.07:

- Twine's strict metadata check passes. Auditwheel reports compatibility with
  manylinux_2_27, which satisfies the selected manylinux_2_28 tag.
- The manifest records CUDA 13.1.115, SM 80/86/89/90/100/120 native code and SM 80
  PTX, with rendering and CUDA bundling enabled.
- ETDRK4 and CNAB2 each execute 17 steps with finite results, both normally and
  with forced PTX execution. The host's earlier CUDA 13.3 PTX failure does not
  occur with this CUDA 13.1 wheel.
- Loaded-library mappings show cudart, NVRTC and NVRTC builtins all coming from
  the extracted wheel with toolkit environment variables cleared.
- All Python files and all 90 native build inputs match the sdist used for that
  build. That archive has since been rebuilt to include the OpenGL fix.

The desktop test caught a real packaging defect: auditwheel bundled private
libGLX, libOpenGL and libGLdispatch libraries, causing CUDA/OpenGL sharing to fail
with an invalid-context error. The local wheel using system graphics libraries
passes on the same display. A temporary diagnostic copy of the manylinux wheel,
changed to use system libGL.so.1, also passes: 76 presentations, 3,785 simulation
steps and a valid 2,583,533-byte PNG export. The original wheel is unchanged.

The Linux CMake configuration now links the system libGL ABI. A fresh local
renderer build confirms a direct libGL.so.1 dependency without split GLVND
libraries. The artifact checker now rejects the original defective wheel, and
`check_linux_wheel.py --gui` exercises desktop rendering and PNG export.
Six packaging/build tests still pass.

Rebuild using the existing Docker image and the updated archive in
`python/dist/pypi-prep/fastturbo-0.1.2.tar.gz`. No Docker image rebuild is needed
for this source change. Repeat the artifact, GPU/PTX and GUI checks on the new
wheel before release. The current file in `python/dist/release/` is the first,
GUI-defective artifact until that rebuild replaces it.

## Source fallback and earlier preparation

- A self-contained sdist builds and can be repacked outside the repository.
  Missing native reference sources are rejected. The sdist passes the contents
  checker and Twine's strict metadata check.
- The native `core_lifecycle` test passes from an isolated source build.
- Local builds with bundling disabled exclude CUDA runtime libraries and use MIT
  metadata. Installing that wheel and running both CLI help commands succeeds.
- The CUDA RPM license is at `/usr/share/licenses/cuda-cudart-13-1/LICENSE`.
  The Dockerfile now stages that runtime's own text at the toolkit-root location
  expected by the bundler; an entrypoint check fails before compilation if absent.
- Existing macOS and Windows artifacts pass the structural checker. They have
  not been rebuilt or runtime-tested on this Linux host.

Before publishing a complete release set, rebuild/validate the macOS and Windows
artifacts at the chosen release version and check the four artifacts together.
The repaired Linux wheel should also be tested on the oldest supported runtime
and a clean machine with an NVIDIA driver but no CUDA Toolkit. The local tests
verify actual loaded library paths, but the host still has a toolkit installed.
