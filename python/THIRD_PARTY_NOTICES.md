# Third-party notices

FastTurbo's own code and documentation are licensed under the MIT license in
`LICENSE`. Bundled third-party components retain the terms listed below.

| Component | Included in | License file |
| --- | --- | --- |
| VkFFT, Dmitrii Tolmachev | All native engines and source archives | `licenses/VkFFT-LICENSE.txt` (MIT) |
| Apple metal-cpp | macOS engine and source archives | `licenses/metal-cpp-LICENSE.txt` (Apache-2.0) |
| nlohmann/json, Niels Lohmann | Windows/Linux engines and source archives | `licenses/json-LICENSE.txt` (MIT) |
| NVIDIA CUDA runtime and NVRTC | Windows wheel | `licenses/CUDA-EULA.txt` (NVIDIA CUDA Toolkit agreement) |

The Windows wheel bundles `cudart64_13.dll`, `nvrtc64_130_0.dll` and
`nvrtc-builtins64_131.dll` in the 0.1.2 release (CUDA 13.1). These NVIDIA components are provided for use by
FastTurbo under the included CUDA agreement; FastTurbo's MIT license does not
relicense them. `LicenseRef-NVIDIA-CUDA` in package metadata refers to that
agreement. A compatible NVIDIA driver is installed separately.

The complete wheel license expression is `MIT AND Apache-2.0` on macOS and
`MIT AND LicenseRef-NVIDIA-CUDA` on Windows. Source archives use
`MIT AND Apache-2.0`; they contain vendored headers but no CUDA runtime DLLs.
All license texts and this notice are installed under the wheel's
`fastturbo-<version>.dist-info/licenses/` directory. Original native build notices
are also retained in `fastturbo/_native/`.

PySide6 and its Qt dependencies are installed separately by pip and retain their
own licenses and notices. They are not embedded in FastTurbo's wheels.
