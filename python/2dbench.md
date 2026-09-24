## Performance

| OS | GPU | Backend | Memory | 2048² → 3072² | steps/s | 4096² → 6144² | steps/s | 4096/2048 |
|---|---|---|---:|---:|---:|---:|---:|---:|
| macOS 15.7.9 | Apple M1 Max | Metal + VkFFT | 64 GB unified | 12.876 ms | 77.7 | 82.376 ms | 12.1 | 6.40× |
| Ubuntu 26.04.1 | RTX 3090 | CUDA + VkFFT | 24 GB | 4.854 ms | 206.0 | 21.689 ms | 46.1 | 4.47× |
| Windows 10 | RTX 3090 | CUDA + VkFFT | 24 GB | 4.943 ms | 202.3 | 21.806 ms | 45.9 | 4.41× |
| Windows 10 | RTX 5090 | CUDA + VkFFT | 31.8 GiB | 1.498 ms | 667.6 | 12.152 ms | 82.3 | 8.11× |
| Ubuntu 24.04.4 LTS | NVIDIA RTX PRO 6000 Blackwell Server Edition | CUDA + VkFFT | 95.0 GiB | 1.312 ms | 762.0 | 12.293 ms | 81.3 | 9.37× |
| Linux | H100 | CUDA + VkFFT | TBD | TBD | TBD | TBD | TBD | TBD |

Times are synchronized wall-clock milliseconds per simulation step. The workload
is float32 ETDRK4 / stress, k₀=10, seed=2, CFL=3, Re=27898.36614974335, with
3/2 padding, batches of eight steps, eight warmup steps and a 20-second target
per size. Initialization, warmup and reset are excluded from timing.

## Relative performance

M1 Max = 1.00×.

| OS / GPU | 2048² | 4096² |
|---|---:|---:|
| macOS / M1 Max | 1.00× | 1.00× |
| Ubuntu / RTX 3090 | 2.65× | 3.80× |
| Windows / RTX 3090 | 2.61× | 3.78× |
| Windows / RTX 5090 | 8.60× | 6.78× |
| Ubuntu / RTX PRO 6000 Blackwell Server Edition | 9.81× | 6.70× |
| Linux / H100 | TBD | TBD |

## RTX 5090 on Windows

The [recorded run](evidence/2dbench/rtx5090-windows.json) from **2026-09-18**
used Windows 10, CUDA 13.4 runtime / driver API, VkFFT 1.3.4 and compute
capability 12.0. CUDA reported 31.8 GiB of GPU memory; the wheel's native library
includes an `sm_120` build.

| Retained → padded | Steps | Seconds | ms/step | steps/s |
|---|---:|---:|---:|---:|
| 2048² → 3072² | 13360 | 20.011 | 1.498 | 667.6 |
| 4096² → 6144² | 1648 | 20.026 | 12.152 | 82.3 |

Against the Windows RTX 3090 values in the table above, the RTX 5090 achieved
**3.30×** the throughput at 2048² and **1.79×** at 4096². Its 4096/2048 timestep
ratio was **8.11×**. These are recorded runs on different systems and CUDA
versions (13.4 versus 13.1), not an isolated GPU-only comparison.

## RTX 3090: Linux vs Windows

| Platform | 2048² → 3072² | 4096² → 6144² |
|---|---:|---:|
| Ubuntu 26.04.1 / CUDA 13.3 | 4.854 ms | 21.689 ms |
| Windows 10 / CUDA 13.1 | 4.943 ms | 21.806 ms |
| Windows / Linux ratio | 1.018× | 1.005× |

The native Ubuntu result is approximately 1.8% faster at 2048² and
0.5% faster at 4096². The CUDA versions differ, so these differences
should not be attributed solely to the operating system.
