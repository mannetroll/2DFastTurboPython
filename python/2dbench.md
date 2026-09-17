# FastTurbo 2D Benchmark Matrix

Commit: `3ce58dd`

Workload: ETDRK4 / stress, k0=10, seed=2, CFL=3, Re=27898.36614974  
Precision: float32  
VkFFT: 1.3.4  
Timing: synchronized wall time, batch=8, warmup=8 steps  
Duration: 20 seconds per case

| GPU | Backend | Memory | 2048² → 3072² | steps/s | 4096² → 6144² | steps/s | 4096/2048 |
|---|---|---:|---:|---:|---:|---:|---:|
| Apple M1 Max | Metal + VkFFT | 64 GB unified | 12.876 ms | 77.7 | 82.376 ms | 12.1 | 6.40× |
| RTX 3090 | CUDA + VkFFT | 24 GB | 4.854 ms | 206.0 | 21.689 ms | 46.1 | 4.47× |
| H100 | CUDA + VkFFT | TBD | TBD | TBD | TBD | TBD | TBD |

## Relative performance

M1 Max = 1.00×.

| GPU | 2048² | 4096² |
|---|---:|---:|
| Apple M1 Max | 1.00× | 1.00× |
| RTX 3090 | 2.65× | 3.80× |
| H100 | TBD | TBD |