# PyPI preparation validation — 2026-09-24

This prepares the release process; no artifacts were uploaded. Version 0.1.2 is
unchanged. Select an unused version and rebuild the release set before publishing.

Validated locally on Ubuntu x86-64, Python 3.14.4, CUDA Toolkit 13.3.73 and an
RTX 3090 with NVIDIA driver 595.91.07:

- Six packaging/build tests pass, including rebuilding an sdist outside the
  repository, missing-source rejection, and relocatable CUDA runtime staging.
- The sdist passes `check_distributions.py` and `twine check --strict`.
- A wheel built from that extracted sdist includes the CUDA runtime, NVRTC and
  builtins. It runs 17 ETDRK4 and 17 CNAB2 steps with finite results. Loaded-library
  mappings confirm all three CUDA libraries come from the extracted wheel.
- The engine has no `DT_NEEDED` entry for `libcuda.so.1`; CUDA driver forwarding
  symbols are hidden, and bundled libraries use relative `$ORIGIN` paths.
- Rebuilding with bundling disabled removes the CUDA libraries from the local
  wheel and produces MIT metadata. Installation and both command help checks pass.
- The native CTest `core_lifecycle` passes from the isolated source build.
- Existing macOS and Windows wheels pass the structural artifact checker. They
  were not rebuilt or runtime-tested on this Linux host.

Outstanding release checks:

- Docker/Podman is unavailable here, so the CUDA 13.1 manylinux container and
  its repaired artifact are not yet validated. Run the manual GitHub workflow
  or the commands in `PUBLISHING.md` on a container-enabled host. The local
  Ubuntu/CUDA 13.3 wheel is correctly reported as `linux_x86_64` by auditwheel
  and is not an upload artifact.
- Forced PTX execution of the local CUDA 13.3 build fails with “the provided PTX
  was compiled with an unsupported toolchain.” Normal cubin execution passes.
  Repeat PTX validation with the release CUDA 13.1 wheel and a compatible driver.
- Validate the repaired release wheel on a clean machine with a driver and no
  CUDA Toolkit, including desktop rendering and the oldest supported runtime
  environment. The local runtime check clears toolkit environment variables and
  verifies loaded paths, but the host still has a toolkit installed.
- Rebuild and validate the macOS and Windows artifacts at the chosen release
  version; check the complete release set before upload.

The locally generated source archive is in
`python/dist/pypi-prep/fastturbo-0.1.2.tar.gz` (ignored build output).
