# Porting contract

Authoritative source: `reference-macos/2DFastTurbo/Sources`, its tests, and
`GUI.png`. The implementation is an unforced decaying flow, not an interactive
fluid painting program. The separate `tools/sim` CNAB2 comparison executable is
not the application integrator. No GUI presets, drag, fixed-dt or batch controls
exist in this reference version; do not invent them from the porting brief.

## Controls and behavior

| Group | Control | Default / choices / semantics |
|---|---|---|
| Header | Focus, Save PNG | F hides inspector and metrics, retains canvas size; Ctrl+S exports 1800² sRGB PNG |
| Initial condition | k₀ | 4; 2,4,6,8,10,12,16,20; resets flow |
| Initial condition | Seed, dice | uint64 2; Enter commits; dice increments with wrap; resets flow |
| Spectral grid | N | 1024; 128,256,512,1024,2048,4096 retained; M=3N/2 |
| Viscosity | Re | 10^(1.066240 log10(N) − .083911 log10(k₀) + 1.286396); ν=1/Re |
| Viscosity | Re slider | log10(Re) in [3,6]; disabled during regulation; live, no reset |
| Viscosity | Regulate with palinstrophy | on; off holds current effective Re |
| Viscosity | P/(Zkmax2) readout | displays 10000 P/(Z kmax²), kmax²=2(N/2−1)² |
| Field | Enstrophy, Vorticity, Speed | Vorticity; display only |
| Palette | Inferno, Ember, Aurora, Glacier | Ember; display only |
| Contrast | Sigma / Exposure | Sigma for vorticity; ±2.5σ, range [1.5,3.5]; Exposure 1.2, range [.25,3]; independent retained values |
| Overlay | Velocity direction | off; 32² instantaneous streamlines, 18 segments, midpoint tracing |
| Numerics | Method | ETD RK4; IMEX RK3, Integrating-factor RK3, ETD RK4; resets; selecting RK3 caps CFL at 1.5 |
| Numerics | Form | Stress; Stress / Advective; resets |
| Numerics | CFL | 3; [1,3]; live; available during automatic Re regulation |
| Transport | Play/Pause, Single step, Reset | starts running; Space, Right, R; reset preserves run/pause intent |
| Interaction | New initial condition | Ctrl+Shift+R increments seed |
| Metrics | Simulated time, Initial turnovers | whole-run advance / active wall seconds; pause/manual steps excluded |
| Metrics | Kinetic energy + history, Enstrophy, Timestep, Integration, Step, GPU allocation | E,Z,dt,recent steps/s,completed step,MiB |

Sliders coalesce live updates to 30 Hz and flush on release/transport. Structural
changes reset state, clock, and history. Paused recoloring reuses the frame.
Inspection runs at 4 Hz; automatic batches adapt from 1 to 32 toward 8 ms and
respect the next 60 Hz snapshot deadline. Hidden/minimized windows keep computing.

Layout in logical pixels: 22 outer horizontal padding, 77 header, 34 field
heading, 196 inspector, 18 panel gap, 54 metrics, 8 metrics gap, 12 bottom.
Minimum square canvas 576, reduced only to fit the monitor. Background
(.035,.047,.073), panel (.061,.076,.106), secondary (.48,.55,.65),
accent (.44,.91,.82); rounded panels and fine translucent borders.

## Numerical pipeline

Domain [0,2π)²; ω=∂xv−∂yu, û=i ky ω̂/k², v̂=−i kx ω̂/k².
State is N² times Fourier-series coefficients, stored N×(N/2+1) complex32.
Mean and both Nyquist axes are excluded; every other retained mode survives.
VkFFT is unnormalized in both directions. Nonlinear workspace uses M=3N/2,
complex pitch ceil((M/2+1)/8)×8. Physical pitch is twice that.

Each stage scatters velocity onto a zero-filled M grid, inverse transforms,
and scales physical samples by 1/N². Stress products are u²−v² and uv (two
inverse + two forward transforms). Advective uses u,v,ωx,ωy and −uωx−vωy
(four inverse + one forward). Gather multiplies product FFT by N²/M².
Stress RHS is −kx ky FFT(u²−v²)+(kx²−ky²)FFT(uv).

Stage zero reduces max(|u|+|v|). dt target=2 CFL/(N max); decreases immediate,
growth min(target,.8 previous+.2 target). Fixed dt and endpoint clipping are
CLI validation features. The timestep stays constant through each full step.
IMEX RK3 uses the reference α,β,ζ coefficients and CN diffusion; IF Heun RK3
uses exponential increments; ETDRK4 uses stable φ1,φ2,φ3 evaluation (16-term
series for |z|≤1). All spectral updates retain compensated Float32 increments;
the clock uses a two-Float compensated sum and exact split endpoint assignment.

Native initialization preserves SplitMix64 sequence, Float32 phase, velocity
modal power exp(−k²/k₀²)/(πk₀²), canonical curl, traversal and conjugate rules.
Initial τ₀=2π sqrt(E₀/Z₀)/sqrt(2E₀). Spectral E,Z,P use Hermitian weights.
Snapshots hold immutable contiguous physical [ω,u,v] planes in three leased
GPU buffers. Only reductions and requested validation/export files reach CPU.

Automatic Re: raw=10000 P/(Zkmax²), two cascaded EMAs with α=1/6; target 20,
error=(filtered−20)/20, deadband .001, gains (.25,0,25), output clipped ±.01,
Re←Re exp(output), bounded by [1000,1e6] and initial calibration /×1000.

Rendering ports all anchors and equations in `TurboRendering.metal`, periodic
bilinear sampling, signed Sigma and tanh Exposure, unsigned exponential
contrast, streamline seeds/taper, and linear HDR highlight expansion up to 2×.
Windows uses CUDA/D3D11 shared buffers, HLSL, Direct2D and DirectWrite.

## Evidence limits

The repository includes an independent FP64 advective reference and analytic
tests, but not the ignored `Benchmarks` Metal endpoint spectra linked by its
README. `tools/sim_512_1001_metal_macOS.txt` belongs to the separate CNAB2 tool;
it cannot validate this application's ETDRK4 trajectory. Windows tests must
distinguish mathematical-reference agreement from a direct Metal capture.
