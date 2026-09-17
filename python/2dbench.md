## Performance

| OS | GPU | Backend | Memory | 2048² → 3072² | steps/s | 4096² → 6144² | steps/s | 4096/2048 |
|---|---|---|---:|---:|---:|---:|---:|---:|
| macOS 15.7.9 | Apple M1 Max | Metal + VkFFT | 64 GB unified | 12.876 ms | 77.7 | 82.376 ms | 12.1 | 6.40× |
| Ubuntu 26.04.1 | RTX 3090 | CUDA + VkFFT | 24 GB | 4.854 ms | 206.0 | 21.689 ms | 46.1 | 4.47× |
| Windows 10 | RTX 3090 | CUDA + VkFFT | 24 GB | 4.943 ms | 202.3 | 21.806 ms | 45.9 | 4.41× |
| Linux | H100 | CUDA + VkFFT | TBD | TBD | TBD | TBD | TBD | TBD |

## Relative performance

M1 Max = 1.00×.

| OS / GPU | 2048² | 4096² |
|---|---:|---:|
| macOS / M1 Max | 1.00× | 1.00× |
| Ubuntu / RTX 3090 | 2.65× | 3.80× |
| Windows / RTX 3090 | 2.61× | 3.78× |
| Linux / H100 | TBD | TBD |

## RTX 3090: Linux vs Windows

| Platform | 2048² → 3072² | 4096² → 6144² |
|---|---:|---:|
| Ubuntu 26.04.1 / CUDA 13.3 | 4.854 ms | 21.689 ms |
| Windows 10 / CUDA 13.1 | 4.943 ms | 21.806 ms |
| Windows / Linux ratio | 1.018× | 1.005× |

The native Ubuntu result is approximately 1.8% faster at 2048² and
0.5% faster at 4096². The CUDA versions differ, so these differences
should not be attributed solely to the operating system.