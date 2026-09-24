# PyPI 0.1.2 preparation validation — 2026-09-24

The three wheels and source archive in `python/dist/` pass the complete release
checks. The Windows wheel was rebuilt and tested on `fractal`; the macOS wheel
was tested on this Mac. The Linux native payload is unchanged from the earlier
Ubuntu validation described below. No artifacts were uploaded to PyPI.

## Final artifacts

| Artifact | Bytes | SHA256 |
| --- | ---: | --- |
| `fastturbo-0.1.2-py3-none-macosx_15_0_arm64.whl` | 5,851,721 | `71b336f42cbd1fe66b638add482cdc10f3e8765d02d552c1798a30cec4ebe5cd` |
| `fastturbo-0.1.2-py3-none-manylinux_2_28_x86_64.whl` | 53,091,445 | `d303e031966883bae417f88b32e21064da42aa08b4a2e8a39947e9dce355958a` |
| `fastturbo-0.1.2-py3-none-win_amd64.whl` | 54,211,255 | `0cf27b30d289b8368b6258ebbfc825f8285a61041f5c1b82bf81e170a2250c23` |
| `fastturbo-0.1.2.tar.gz` | 7,938,040 | `2824a3e9429305e204a9214af0301803debaa43a6d73b8ceedee7a74ef30f9f7` |

## Windows build and runtime

The Windows wheel was built from the prepared source archive in a temporary
directory on `fractal`, using CUDA 13.4.92 and the MSVC x64 tools. The existing
checkout and installed application were not modified.

- The wheel contains `fastturbo.dll`, `cudart64_13.dll`, `nvrtc64_130_0.dll`
  and `nvrtc-builtins64_134.dll`. The obsolete CUDA 13.1 builtins are absent.
- Both packaged CUDA EULA copies match the selected toolkit. The README and
  third-party notices describe CUDA 13.4 on Windows and CUDA 13.1 on Linux.
- All 79 recorded native build inputs match the final source archive. Raw and
  LF-normalized hashes are retained, allowing mixed Windows/Unix line endings
  without changing the source comparison. The three earlier raw-hash
  discrepancies were traced to mixed line endings; this clean rebuild replaces
  that artifact.
- On Python 3.13.11, RTX 3090 and NVIDIA driver 616.92, the extracted wheel
  executes 17 finite timesteps at retained resolution 34 / padded resolution 51,
  both normally and with forced PTX JIT and the CUDA cache disabled. CUDA driver
  and runtime APIs both report 13.4.
- With toolkit paths and CUDA environment variables cleared, the desktop check
  passes at resolution 2048 with 41 presentations, 69 simulation steps and finite
  diagnostics. It ran in the logged-in desktop session because an SSH session
  does not provide the same graphics context. The temporary test task was removed.

No `2dbench` performance workload was run.

## macOS runtime

The final macOS wheel passes isolated runtime checks on Apple M1 Max with Python
3.14.4. ETDRK4 and CNAB2 each execute 17 finite timesteps using the KM3 spectrum,
reset successfully, and export nonempty PNG images. Execution uses the wheel's
native resources outside the repository with build tools excluded from PATH.

The wheel targets arm64 macOS 15 with Swift 6.2.4. Its 152 native build inputs
match the source archive; two Finder `.DS_Store` entries are excluded from this
comparison. Every `_native/` entry is byte-for-byte unchanged from the previously
built wheel with SHA256
`62db5540e678bcad33ff4f87af048651e7e22a0127ecc93ace160c3e1a6ad10a`.
The refresh updates the shared Windows build helper, README, notices and RECORD.

## Linux runtime evidence

The Linux native payload is byte-for-byte unchanged from the earlier validated
wheel with SHA256
`b5fdfe974ecde26aff37cc64bb46eef720050aa2875467d05cd572ae17b75c5b`.
The final wheel refresh updates the shared Windows build helper, README, notices,
RECORD and the dist-info EULA to match the bundled runtime's agreement. All 90
recorded native build inputs match the final source archive.

The earlier native Ubuntu x86-64 validation used Python 3.14.4, RTX 3090 and
NVIDIA driver 595.91.07:

- ETDRK4 and CNAB2 each executed 17 finite timesteps, normally and with forced
  PTX execution. Loaded-library mappings confirmed that CUDA runtime, NVRTC and
  builtins came from the extracted wheel with toolkit variables cleared.
- The desktop check passed with 77 presentations and a 2,606,284-byte PNG.
- The renderer linked system `libGL.so.1`, with no private GLX/OpenGL dispatch
  libraries bundled. Auditwheel reported manylinux_2_27 symbol compatibility,
  satisfying the selected manylinux_2_28 tag.

The native build uses CUDA 13.1.115, SM 80/86/89/90/100/120 machine code and SM 80
PTX. Linux GPU tests were not rerun during this macOS/Windows preparation.

## Packaging checks

- Strict Twine checks pass for all four final artifacts.
- The complete release-set checker passes: one version, correct platform tags,
  native resources, runtime libraries, matching EULAs, matching shared Python
  modules, and recorded native inputs matching the source archive.
- Every wheel RECORD hash and file size matches its contents. All four README
  descriptions match `python/README.md`, allowing Windows CRLF line endings.
- Packaging regression tests: **18 passed, 1 skipped** on macOS. They cover
  isolated sdist rebuilding, missing source rejection, stale runtime cleanup,
  toolkit upgrade staging, EULA staging and source consistency. The skipped test
  specifically requires Linux ELF runtime staging.

Run the final checks from `python/` (`readelf` or `llvm-readelf` must be on PATH
to inspect the Linux wheel):

```sh
python scripts/check_distributions.py --release-set dist/*.whl dist/*.tar.gz
uvx twine check --strict dist/*.whl dist/*.tar.gz
```

The GPU test hosts have toolkits installed; isolated runtime tests removed their
paths rather than uninstalling them. The oldest supported OS/PySide6 combinations
and a completely toolkit-free host have not been exercised. PyPI reported latest
version 0.1.1 and no 0.1.2 files during preparation; verify filename availability
again immediately before uploading.
