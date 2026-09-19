// Derived from FastTurboShaders.swift. CUDA syntax port; see scripts/port_kernels.py.
#pragma once
#include "cuda_support.cuh"
using uint = unsigned int;
struct Params {
    uint n, m, integrator, stage;
    float viscosity, cfl, fixedDT, inverseN2;
    float endHigh, endLow;
    uint hasEnd, groups;
    uint nonlinearFormulation;
};
struct ClockState {
    float timeHigh, timeLow, dt, controllerDT;
    unsigned long long steps;
    float maxSpeed;
    uint valid;
    uint active, padding;
};
__device__ inline int wave(uint i, uint n) { return i <= n / 2 ? int(i) : int(i) - int(n); }
__device__ inline bool retained(int x, int y, uint n) {
    return abs(x) < int(n / 2) && abs(y) < int(n / 2) && (x != 0 || y != 0);
}
__device__ inline V2 timesI(V2 z) { return V2(-z.y, z.x); }

__global__ void projectState(const V2 *input , V2 *omega ,
                         Params p) {
    uint2 gid = make_uint2(blockIdx.x*blockDim.x+threadIdx.x, blockIdx.y*blockDim.y+threadIdx.y);
    uint width = p.n / 2 + 1;
    if (gid.x >= width || gid.y >= p.n) return;
    omega[gid.y * width + gid.x] = retained(int(gid.x), wave(gid.y, p.n), p.n)
        ? input[gid.y * p.n + gid.x] : V2(0);
}

// The state carries N² times the Fourier-series coefficients. VkFFT is unnormalized
// in both directions, so velocities are scaled by 1/N² only after their inverse.
// Only the main padded workspace uses this pitch. The logical spectrum width
// remains M/2+1, and the physical row contains exactly M transformed samples.
__device__ inline uint paddedComplexPitch(Params p) { return ((p.m / 2 + 1 + 7) / 8) * 8; }

__global__ void velocityToPadded(const V2 *omega ,
                             V2 *padded , Params p) {
    uint2 gid = make_uint2(blockIdx.x*blockDim.x+threadIdx.x, blockIdx.y*blockDim.y+threadIdx.y);
    uint width = p.m / 2 + 1;
    if (gid.x >= width || gid.y >= p.m) return;
    uint pitch = paddedComplexPitch(p);
    uint index = gid.y * pitch + gid.x;
    uint stride = p.m * pitch;
    int x = int(gid.x), y = wave(gid.y, p.m);
    if (!retained(x, y, p.n)) {
        padded[index] = 0; padded[stride + index] = 0;
        if (p.nonlinearFormulation == 1) {
            padded[2 * stride + index] = 0; padded[3 * stride + index] = 0;
        }
        return;
    }
    uint sy = y < 0 ? uint(y + int(p.n)) : uint(y);
    V2 iw = timesI(omega[sy * (p.n / 2 + 1) + gid.x]);
    float k2 = float(x * x + y * y);
    // Canonical curl: omega = dx(v) - dy(u).
    padded[index] = iw * (float(y) / k2);
    padded[stride + index] = iw * (-float(x) / k2);
    if (p.nonlinearFormulation == 1) {
        padded[2 * stride + index] = iw * float(x);
        padded[3 * stride + index] = iw * float(y);
    }
}

// Stage zero needs both the velocity maximum and nonlinear products. Read and
// normalize each input once, then overwrite only this thread's own samples.
__global__ void reduceCFLAndProducts(float *padded , V2 *partials ,
                      Params p) {
    uint tid = blockIdx.x*blockDim.x+threadIdx.x;
    uint local = threadIdx.x;
    uint group = blockIdx.x;
    __shared__ float maxima[256];
    __shared__ uint invalid[256];
    uint row = 2 * paddedComplexPitch(p), stride = p.m * row;
    float best = 0; uint bad = 0;
    for (uint i = tid; i < p.m * p.m; i += p.groups * 256) {
        uint j = (i / p.m) * row + i % p.m;
        float u = padded[j] * p.inverseN2, v = padded[stride + j] * p.inverseN2;
        bad |= uint(!isfinite(u) || !isfinite(v));
        best = max(best, fabsf(u) + fabsf(v));
        if (p.nonlinearFormulation == 0) {
            padded[j] = u * u - v * v;
            padded[stride + j] = u * v;
        } else {
            float wx = padded[2 * stride + j] * p.inverseN2;
            float wy = padded[3 * stride + j] * p.inverseN2;
            bad |= uint(!isfinite(wx) || !isfinite(wy));
            padded[j] = -(u * wx + v * wy);
        }
    }
    maxima[local] = best; invalid[local] = bad;
    __syncthreads();
    for (uint s = 128; s > 0; s >>= 1) {
        if (local < s) { maxima[local] = max(maxima[local], maxima[local + s]); invalid[local] |= invalid[local + s]; }
        __syncthreads();
    }
    if (local == 0) partials[group] = V2(maxima[0], float(invalid[0]));
}

__global__ void selectTimeStep(const V2 *partials ,
                           ClockState *state , Params p) {
    uint tid = blockIdx.x*blockDim.x+threadIdx.x;
    ClockState &clock = *state;
    if (tid != 0) return;
    float best = 0; bool bad = clock.valid == 0;
    for (uint i = 0; i < p.groups; ++i) { best = max(best, partials[i].x); bad |= partials[i].y != 0; }
    clock.maxSpeed = best;
    clock.active = 0;
    if (bad) { atomicExch(&clock.valid, 0u); return; }
    float remaining = (p.endHigh - clock.timeHigh) + (p.endLow - clock.timeLow);
    if (p.hasEnd && remaining <= 0) return;
    float proposed;
    if (p.fixedDT > 0) {
        proposed = p.fixedDT;
    } else {
        float target = best > 0 ? 2 * p.cfl / (float(p.n) * best)
                                : (clock.controllerDT > 0 ? clock.controllerDT : 1.0f);
        // Reductions in the stability bound take effect immediately. Growth is
        // smoothed with the legacy 0.8/0.2 rule, independent of batch size.
        proposed = clock.controllerDT > 0 ? min(target, 0.8f * clock.controllerDT + 0.2f * target) : target;
    }
    if (!(proposed > 0) || !isfinite(proposed)) { atomicExch(&clock.valid, 0u); return; }
    clock.controllerDT = proposed;
    clock.active = p.hasEnd && proposed >= remaining ? 2 : 1;
    clock.dt = p.hasEnd ? min(proposed, remaining) : proposed;
}

__global__ void nonlinearProducts(float *padded , Params p) {
    uint2 gid = make_uint2(blockIdx.x*blockDim.x+threadIdx.x, blockIdx.y*blockDim.y+threadIdx.y);
    if (gid.x >= p.m || gid.y >= p.m) return;
    uint row = 2 * paddedComplexPitch(p), stride = p.m * row, j = gid.y * row + gid.x;
    float u = padded[j] * p.inverseN2, v = padded[stride + j] * p.inverseN2;
    if (p.nonlinearFormulation == 0) {
        padded[j] = u * u - v * v;
        padded[stride + j] = u * v;
    } else {
        float wx = padded[2 * stride + j] * p.inverseN2;
        float wy = padded[3 * stride + j] * p.inverseN2;
        padded[j] = -(u * wx + v * wy);
    }
}

// phi_k(z) = sum_j z^j/(j+k)!; the series prevents cancellation near zero.
// All arguments are nonpositive. Large negative arguments use stable recurrences.
__device__ inline V3 phi123(float z) {
    if (fabsf(z) <= 1.0f) {
        V3 term = V3(1, 0.5f, 1.0f / 6.0f), sum = term;
        for (uint j = 1; j <= 16; ++j) {
            term *= z / V3(float(j + 1), float(j + 2), float(j + 3));
            sum += term;
        }
        return sum;
    }
    float a = (expf(z) - 1) / z;
    float b = (a - 1) / z;
    return V3(a, b, (b - 0.5f) / z);
}

// Small physical increments can be much smaller than one ulp of a spectral
// coefficient. Keep the lost part across steps instead of accumulating a
// timestep-dependent bias. Safe math mode prevents reassociation here.
__device__ inline V2 compensatedAdd(V2 value, V2 increment, V2 &remainder) {
    V2 corrected = increment - remainder;
    V2 result = value + corrected;
    remainder = (result - value) - corrected;
    return result;
}

__global__ void advanceStage(V2 *omega , const V2 *padded ,
                         V2 *base , V2 *first ,
                         V2 *second , V2 *aux ,
                         float *coefficients , ClockState *state ,
                         V2 *remainder , Params p) {
    uint2 gid = make_uint2(blockIdx.x*blockDim.x+threadIdx.x, blockIdx.y*blockDim.y+threadIdx.y);
    ClockState &clock = *state;
    uint stateWidth = p.n / 2 + 1;
    if (gid.x >= stateWidth || gid.y >= p.n || clock.active == 0) return;
    uint index = gid.y * stateWidth + gid.x;
    int x = int(gid.x), y = wave(gid.y, p.n);
    if (!retained(x, y, p.n)) { omega[index] = 0; remainder[index] = 0; return; }
    uint pitch = paddedComplexPitch(p), stride = p.m * pitch;
    uint j = uint(y < 0 ? y + int(p.m) : y) * pitch + gid.x;
    V2 f;
    if (p.nonlinearFormulation == 0) {
        V2 diagonal = padded[j], shear = padded[stride + j];
        // Curl of -div(u tensor u), with canonical omega, hence the negative
        // of Metal2DTurbo's legacy stress combination.
        f = -float(x * y) * diagonal + float(x * x - y * y) * shear;
    } else {
        // Advective products already contain -u*dx(omega)-v*dy(omega).
        // Gathering adds no wave-number multiplier to product FFT roundoff.
        f = padded[j];
    }
    f *= float(p.n * p.n) / float(p.m * p.m);
    float dt = clock.dt, k2 = float(x * x + y * y);
    if (p.integrator == 0) {
        float alpha = p.stage == 0 ? 8.0f / 15 : (p.stage == 1 ? 2.0f / 15 : 1.0f / 3);
        float beta = p.stage == 0 ? 8.0f / 15 : (p.stage == 1 ? 5.0f / 12 : 3.0f / 4);
        float zeta = p.stage == 1 ? -17.0f / 60 : -5.0f / 12;
        float viscous = 0.5f * alpha * dt * p.viscosity * k2;
        V2 nonlinear = beta * f;
        if (p.stage > 0) nonlinear += zeta * first[index];
        V2 increment = (dt * nonlinear - 2 * viscous * omega[index]) / (1 + viscous);
        omega[index] = compensatedAdd(omega[index], increment, remainder[index]);
        first[index] = f;
    } else if (p.integrator == 1) {
        uint coefficientStride = p.n * stateWidth;
        if (p.stage == 0) {
            float z = -p.viscosity * dt * k2;
            float third = (z / 3) * phi123(z / 3).x;
            coefficients[index] = third;
            coefficients[coefficientStride + index] = (2 * z / 3) * phi123(2 * z / 3).x;
            coefficients[2 * coefficientStride + index] = z * phi123(z).x;
            base[index] = omega[index]; first[index] = f;
            omega[index] = base[index] + (third * base[index] + (dt / 3) * (f + third * f));
        } else if (p.stage == 1) {
            omega[index] = base[index] + (coefficients[coefficientStride + index] * base[index]
                           + (2 * dt / 3) * (f + coefficients[index] * f));
        } else {
            V2 increment = coefficients[2 * coefficientStride + index] * base[index]
                               + (dt / 4) * (first[index] + coefficients[2 * coefficientStride + index] * first[index])
                               + (3 * dt / 4) * (f + coefficients[index] * f);
            omega[index] = compensatedAdd(base[index], increment, remainder[index]);
        }
    } else {
        uint coefficientStride = p.n * stateWidth;
        if (p.stage == 0) {
            float z = -p.viscosity * dt * k2;
            V3 phi = phi123(z);
            float halfPhi = phi123(z / 2).x;
            float e = z * phi.x, e2 = (z / 2) * halfPhi;
            float q = 0.5f * dt * halfPhi;
            coefficients[index] = e; coefficients[coefficientStride + index] = e2; coefficients[2 * coefficientStride + index] = q;
            coefficients[3 * coefficientStride + index] = dt * (phi.x - 3 * phi.y + 4 * phi.z);
            coefficients[4 * coefficientStride + index] = dt * (phi.y - 2 * phi.z);
            coefficients[5 * coefficientStride + index] = dt * (-phi.y + 4 * phi.z);
            base[index] = omega[index]; first[index] = f;
            aux[index] = base[index] + (e2 * base[index] + q * f);
            omega[index] = aux[index];
        } else if (p.stage == 1) {
            second[index] = f;
            omega[index] = base[index] + (coefficients[coefficientStride + index] * base[index] + coefficients[2 * coefficientStride + index] * f);
        } else if (p.stage == 2) {
            second[index] += f;
            omega[index] = aux[index] + (coefficients[coefficientStride + index] * aux[index] + coefficients[2 * coefficientStride + index] * (2 * f - first[index]));
        } else {
            V2 increment = coefficients[index] * base[index] + coefficients[3 * coefficientStride + index] * first[index]
                               + 2 * coefficients[4 * coefficientStride + index] * second[index] + coefficients[5 * coefficientStride + index] * f;
            omega[index] = compensatedAdd(base[index], increment, remainder[index]);
        }
    }
    if (!finite(omega[index])) atomicExch(&clock.valid, 0u);
}

__global__ void commitStep(ClockState *state , Params p) {
    uint tid = blockIdx.x*blockDim.x+threadIdx.x;
    ClockState &clock = *state;
    if (tid != 0 || clock.active == 0 || clock.valid == 0) return;
    if (clock.active == 2) {
        clock.timeHigh = p.endHigh; clock.timeLow = p.endLow;
    } else {
        // TwoSum followed by renormalization retains small steps after a long run.
        float s = clock.timeHigh + clock.dt;
        float b = s - clock.timeHigh;
        float e = (clock.timeHigh - (s - b)) + (clock.dt - b);
        float low = clock.timeLow + e;
        float high = s + low;
        clock.timeLow = low - (high - s);
        clock.timeHigh = high;
    }
    clock.steps += 1;
}

__global__ void spectralStatistics(const V2 *omega ,
                               float4 *partials , Params p) {
    uint tid = blockIdx.x*blockDim.x+threadIdx.x;
    uint local = threadIdx.x;
    uint group = blockIdx.x;
    __shared__ float energies[256], enstrophies[256], palinstrophies[256];
    __shared__ uint invalid[256];
    float energy = 0, enstrophy = 0, palinstrophy = 0; uint bad = 0;
    uint width = p.n / 2 + 1;
    for (uint i = tid; i < p.n * width; i += p.groups * 256) {
        V2 w = omega[i] * p.inverseN2;
        bad |= uint(!finite(w));
        int x = int(i % width), y = wave(i / width, p.n);
        // Interior columns also represent their negative-kx conjugates.
        // kx=0 (and the excluded Nyquist column) already hold both ky signs.
        float weight = x == 0 || x == int(p.n / 2) ? 1.0f : 2.0f;
        float k2 = float(x * x + y * y), power = weight * (0.5f * dot(w, w));
        enstrophy += power;
        palinstrophy += k2 * power;
        if (k2 > 0) energy += power / k2;
    }
    energies[local] = energy; enstrophies[local] = enstrophy; palinstrophies[local] = palinstrophy; invalid[local] = bad;
    __syncthreads();
    for (uint s = 128; s > 0; s >>= 1) {
        if (local < s) { energies[local] += energies[local + s]; enstrophies[local] += enstrophies[local + s]; palinstrophies[local] += palinstrophies[local + s]; invalid[local] |= invalid[local + s]; }
        __syncthreads();
    }
    if (local == 0) partials[group] = make_float4(energies[0], enstrophies[0], float(invalid[0]), palinstrophies[0]);
}

__global__ void prepareSnapshot(const V2 *omega , V2 *spectra ,
                            Params p) {
    uint2 gid = make_uint2(blockIdx.x*blockDim.x+threadIdx.x, blockIdx.y*blockDim.y+threadIdx.y);
    uint width = p.n / 2 + 1;
    if (gid.x >= width || gid.y >= p.n) return;
    uint i = gid.y * width + gid.x, stride = p.n * width;
    V2 w = omega[i];
    spectra[i] = w;
    if (p.stage == 3) {
        int x = int(gid.x), y = wave(gid.y, p.n);
        if(p.integrator==3&&gid.y==p.n/2)y=-int(p.n/2);
        float k2 = float(x * x + y * y);
        V2 iw = timesI(w);
        spectra[stride + i] = k2 > 0 ? iw * (float(y) / k2) : V2(0);
        spectra[2 * stride + i] = k2 > 0 ? iw * (-float(x) / k2) : V2(0);
    }
    // The application samples CNAB2 on the retained N grid. Its compact
    // vertical cutoff is self-conjugate here: take the real part separately
    // for vorticity and each velocity component, after reconstruction.
    if(p.integrator==3&&gid.x==0&&gid.y==p.n/2){
        spectra[i].y=0;
        if(p.stage==3){spectra[stride+i].y=0;spectra[2*stride+i].y=0;}
    }
}

__global__ void extractSnapshot(const float *padded , float *fields ,
                            float4 *partials , Params p) {
    uint tid = blockIdx.x*blockDim.x+threadIdx.x;
    uint local = threadIdx.x;
    uint group = blockIdx.x;
    __shared__ float maxima[256], speeds[256], sums[256], squares[256];
    __shared__ uint invalid[256];
    uint count = p.n * p.n, row = 2 * (p.n / 2 + 1), stride = p.n * row;
    float maxOmega = 0, maxSpeed = 0, sumOmega = 0, sumOmegaSquared = 0; uint bad = 0;
    for (uint i = tid; i < count; i += p.groups * 256) {
        uint j = (i / p.n) * row + i % p.n;
        float w = padded[j] * p.inverseN2;
        float u = p.stage == 3 ? padded[stride + j] * p.inverseN2 : 0;
        float v = p.stage == 3 ? padded[2 * stride + j] * p.inverseN2 : 0;
        fields[i] = w; fields[count + i] = u; fields[2 * count + i] = v;
        maxOmega = max(maxOmega, fabsf(w)); maxSpeed = max(maxSpeed, length(V2(u, v)));
        sumOmega += w; sumOmegaSquared += w * w;
        bad |= uint(!isfinite(w) || !isfinite(u) || !isfinite(v));
    }
    maxima[local] = maxOmega; speeds[local] = maxSpeed; invalid[local] = bad;
    sums[local] = sumOmega; squares[local] = sumOmegaSquared;
    __syncthreads();
    for (uint s = 128; s > 0; s >>= 1) {
        if (local < s) {
            maxima[local] = max(maxima[local], maxima[local + s]); speeds[local] = max(speeds[local], speeds[local + s]);
            invalid[local] |= invalid[local + s];
            sums[local] += sums[local + s]; squares[local] += squares[local + s];
        }
        __syncthreads();
    }
    if (local == 0) {
        partials[group] = make_float4(maxima[0], speeds[0], float(invalid[0]), 0);
        partials[p.groups + group] = make_float4(sums[0], squares[0], 0, 0);
    }
}
