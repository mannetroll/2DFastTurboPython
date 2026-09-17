# FastTurbo 2D Benchmark Matrix

Commit: `3ce58dd`

Workload: ETDRK4 / stress, k0=10, seed=2, CFL=3, Re=27898.36614974  
Precision: float32  
VkFFT: 1.3.4  
Timing: synchronized wall time, batch=8, warmup=8 steps  
Duration: 20 seconds per case

## Performance

| OS | GPU | Backend | Memory | 2048² → 3072² | steps/s | 4096² → 6144² | steps/s | 4096/2048 |
|---|---|---|---:|---:|---:|---:|---:|---:|
| macOS 15.7.9 | Apple M1 Max | Metal + VkFFT | 64 GB unified | 12.876 ms | 77.7 | 82.376 ms | 12.1 | 6.40× |
| Ubuntu 26.04.1 | RTX 3090 | CUDA + VkFFT | 24 GB | 4.854 ms | 206.0 | 21.689 ms | 46.1 | 4.47× |
| Windows 10 | RTX 3090 | CUDA + VkFFT | 24 GB | TBD | TBD | TBD | TBD | TBD |
| Ubuntu / Linux | H100 | CUDA + VkFFT | TBD | TBD | TBD | TBD | TBD | TBD |

## Relative performance

M1 Max = 1.00×.

| OS / GPU | 2048² | 4096² |
|---|---:|---:|
| macOS / M1 Max | 1.00× | 1.00× |
| Ubuntu / RTX 3090 | 2.65× | 3.80× |
| Windows / RTX 3090 | TBD | TBD |
| Linux / H100 | TBD | TBD |

## Platform comparison

The RTX 3090 is tested on both native Ubuntu and native Windows using the
same FastTurbo workload. This isolates the effect of the operating-system
and platform-specific native CUDA integration more closely than the
cross-GPU comparisons.

| RTX 3090 | 2048² | 4096² |
|---|---:|---:|
| Ubuntu 26.04.1 | 4.854 ms | 21.689 ms |
| Windows 10 | TBD | TBD |
| Windows / Linux ratio | TBD | TBD |