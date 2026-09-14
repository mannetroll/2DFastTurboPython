import Foundation

enum FastTurboShaders {
    static let source = #"""
    #include <metal_stdlib>
    using namespace metal;

    // Preserve the Params buffer layout while specializing immutable configuration.
    constant uint specializedN [[function_constant(0)]];
    constant uint specializedM [[function_constant(1)]];
    constant uint specializedIntegrator [[function_constant(2)]];
    constant uint specializedNonlinearFormulation [[function_constant(3)]];

    struct Params {
        uint n, m, integrator, stage;
        float viscosity, cfl, fixedDT, inverseN2;
        float endHigh, endLow;
        uint hasEnd, groups;
        uint nonlinearFormulation;
    };
    struct ClockState {
        float timeHigh, timeLow, dt, controllerDT;
        ulong steps;
        float maxSpeed;
        atomic_uint valid;
        uint active, padding;
    };
    inline int wave(uint i, uint n) { return i <= n / 2 ? int(i) : int(i) - int(n); }
    inline bool retained(int x, int y, uint n) {
        return abs(x) < int(n / 2) && abs(y) < int(n / 2) && (x != 0 || y != 0);
    }
    inline float2 timesI(float2 z) { return float2(-z.y, z.x); }

    kernel void projectState(device const float2 *input [[buffer(0)]], device float2 *omega [[buffer(1)]],
                             constant Params &p [[buffer(2)]],
                             uint2 gid [[thread_position_in_grid]]) {
        uint width = specializedN / 2 + 1;
        if (gid.x >= width || gid.y >= specializedN) return;
        omega[gid.y * width + gid.x] = retained(int(gid.x), wave(gid.y, specializedN), specializedN)
            ? input[gid.y * specializedN + gid.x] : float2(0);
    }

    // The state carries N² times the Fourier-series coefficients. VkFFT is unnormalized
    // in both directions, so velocities are scaled by 1/N² only after their inverse.
    // Only the main padded workspace uses this pitch. The logical spectrum width
    // remains M/2+1, and the physical row contains exactly M transformed samples.
    inline uint paddedComplexPitch() { return ((specializedM / 2 + 1 + 7) / 8) * 8; }

    kernel void velocityToPadded(device const float2 *omega [[buffer(0)]],
                                 device float2 *padded [[buffer(1)]], constant Params &p [[buffer(2)]],
                                 uint2 gid [[thread_position_in_grid]]) {
        uint width = specializedM / 2 + 1;
        if (gid.x >= width || gid.y >= specializedM) return;
        uint pitch = paddedComplexPitch();
        uint index = gid.y * pitch + gid.x;
        uint stride = specializedM * pitch;
        int x = int(gid.x), y = wave(gid.y, specializedM);
        if (!retained(x, y, specializedN)) {
            padded[index] = 0; padded[stride + index] = 0;
            if (specializedNonlinearFormulation == 1) {
                padded[2 * stride + index] = 0; padded[3 * stride + index] = 0;
            }
            return;
        }
        uint sy = y < 0 ? uint(y + int(specializedN)) : uint(y);
        float2 iw = timesI(omega[sy * (specializedN / 2 + 1) + gid.x]);
        float k2 = float(x * x + y * y);
        // Canonical curl: omega = dx(v) - dy(u).
        padded[index] = iw * (float(y) / k2);
        padded[stride + index] = iw * (-float(x) / k2);
        if (specializedNonlinearFormulation == 1) {
            padded[2 * stride + index] = iw * float(x);
            padded[3 * stride + index] = iw * float(y);
        }
    }

    // Stage zero needs both the velocity maximum and nonlinear products. Read and
    // normalize each input once, then overwrite only this thread's own samples.
    kernel void reduceCFLAndProducts(device float *padded [[buffer(0)]], device float2 *partials [[buffer(1)]],
                          constant Params &p [[buffer(2)]], uint tid [[thread_position_in_grid]],
                          uint local [[thread_index_in_threadgroup]], uint group [[threadgroup_position_in_grid]]) {
        threadgroup float maxima[256];
        threadgroup uint invalid[256];
        uint row = 2 * paddedComplexPitch(), stride = specializedM * row;
        float best = 0; uint bad = 0;
        for (uint i = tid; i < specializedM * specializedM; i += p.groups * 256) {
            uint j = (i / specializedM) * row + i % specializedM;
            float u = padded[j] * p.inverseN2, v = padded[stride + j] * p.inverseN2;
            bad |= uint(!isfinite(u) || !isfinite(v));
            best = max(best, abs(u) + abs(v));
            if (specializedNonlinearFormulation == 0) {
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
        threadgroup_barrier(mem_flags::mem_threadgroup);
        for (uint s = 128; s > 0; s >>= 1) {
            if (local < s) { maxima[local] = max(maxima[local], maxima[local + s]); invalid[local] |= invalid[local + s]; }
            threadgroup_barrier(mem_flags::mem_threadgroup);
        }
        if (local == 0) partials[group] = float2(maxima[0], float(invalid[0]));
    }

    kernel void selectTimeStep(device const float2 *partials [[buffer(0)]],
                               device ClockState &clock [[buffer(1)]], constant Params &p [[buffer(2)]],
                               uint tid [[thread_position_in_grid]]) {
        if (tid != 0) return;
        float best = 0; bool bad = atomic_load_explicit(&clock.valid, memory_order_relaxed) == 0;
        for (uint i = 0; i < p.groups; ++i) { best = max(best, partials[i].x); bad |= partials[i].y != 0; }
        clock.maxSpeed = best;
        clock.active = 0;
        if (bad) { atomic_store_explicit(&clock.valid, 0u, memory_order_relaxed); return; }
        float remaining = (p.endHigh - clock.timeHigh) + (p.endLow - clock.timeLow);
        if (p.hasEnd && remaining <= 0) return;
        float proposed;
        if (p.fixedDT > 0) {
            proposed = p.fixedDT;
        } else {
            float target = best > 0 ? 2 * p.cfl / (float(specializedN) * best)
                                    : (clock.controllerDT > 0 ? clock.controllerDT : 1.0f);
            // Reductions in the stability bound take effect immediately. Growth is
            // smoothed with the legacy 0.8/0.2 rule, independent of batch size.
            proposed = clock.controllerDT > 0 ? min(target, 0.8f * clock.controllerDT + 0.2f * target) : target;
        }
        if (!(proposed > 0) || !isfinite(proposed)) { atomic_store_explicit(&clock.valid, 0u, memory_order_relaxed); return; }
        clock.controllerDT = proposed;
        clock.active = p.hasEnd && proposed >= remaining ? 2 : 1;
        clock.dt = p.hasEnd ? min(proposed, remaining) : proposed;
    }

    kernel void nonlinearProducts(device float *padded [[buffer(0)]], constant Params &p [[buffer(1)]],
                               uint2 gid [[thread_position_in_grid]]) {
        if (gid.x >= specializedM || gid.y >= specializedM) return;
        uint row = 2 * paddedComplexPitch(), stride = specializedM * row, j = gid.y * row + gid.x;
        float u = padded[j] * p.inverseN2, v = padded[stride + j] * p.inverseN2;
        if (specializedNonlinearFormulation == 0) {
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
    inline float3 phi123(float z) {
        if (abs(z) <= 1.0f) {
            float3 term = float3(1, 0.5f, 1.0f / 6.0f), sum = term;
            for (uint j = 1; j <= 16; ++j) {
                term *= z / float3(float(j + 1), float(j + 2), float(j + 3));
                sum += term;
            }
            return sum;
        }
        float a = (exp(z) - 1) / z;
        float b = (a - 1) / z;
        return float3(a, b, (b - 0.5f) / z);
    }

    // Small physical increments can be much smaller than one ulp of a spectral
    // coefficient. Keep the lost part across steps instead of accumulating a
    // timestep-dependent bias. Safe math mode prevents reassociation here.
    inline float2 compensatedAdd(float2 value, float2 increment, device float2 &remainder) {
        float2 corrected = increment - remainder;
        float2 result = value + corrected;
        remainder = (result - value) - corrected;
        return result;
    }

    kernel void advanceStage(device float2 *omega [[buffer(0)]], device const float2 *padded [[buffer(1)]],
                             device float2 *base [[buffer(2)]], device float2 *first [[buffer(3)]],
                             device float2 *second [[buffer(4)]], device float2 *aux [[buffer(5)]],
                             device float *coefficients [[buffer(6)]], device ClockState &clock [[buffer(7)]],
                             device float2 *remainder [[buffer(8)]], constant Params &p [[buffer(9)]],
                             uint2 gid [[thread_position_in_grid]]) {
        uint stateWidth = specializedN / 2 + 1;
        if (gid.x >= stateWidth || gid.y >= specializedN || clock.active == 0) return;
        uint index = gid.y * stateWidth + gid.x;
        int x = int(gid.x), y = wave(gid.y, specializedN);
        if (!retained(x, y, specializedN)) { omega[index] = 0; remainder[index] = 0; return; }
        uint pitch = paddedComplexPitch(), stride = specializedM * pitch;
        uint j = uint(y < 0 ? y + int(specializedM) : y) * pitch + gid.x;
        float2 f;
        if (specializedNonlinearFormulation == 0) {
            float2 diagonal = padded[j], shear = padded[stride + j];
            // Curl of -div(u tensor u), with canonical omega, hence the negative
            // of Metal2DTurbo's legacy stress combination.
            f = -float(x * y) * diagonal + float(x * x - y * y) * shear;
        } else {
            // Advective products already contain -u*dx(omega)-v*dy(omega).
            // Gathering adds no wave-number multiplier to product FFT roundoff.
            f = padded[j];
        }
        f *= float(specializedN * specializedN) / float(specializedM * specializedM);
        float dt = clock.dt, k2 = float(x * x + y * y);
        if (specializedIntegrator == 0) {
            float alpha = p.stage == 0 ? 8.0f / 15 : (p.stage == 1 ? 2.0f / 15 : 1.0f / 3);
            float beta = p.stage == 0 ? 8.0f / 15 : (p.stage == 1 ? 5.0f / 12 : 3.0f / 4);
            float zeta = p.stage == 1 ? -17.0f / 60 : -5.0f / 12;
            float viscous = 0.5f * alpha * dt * p.viscosity * k2;
            float2 nonlinear = beta * f;
            if (p.stage > 0) nonlinear += zeta * first[index];
            float2 increment = (dt * nonlinear - 2 * viscous * omega[index]) / (1 + viscous);
            omega[index] = compensatedAdd(omega[index], increment, remainder[index]);
            first[index] = f;
        } else if (specializedIntegrator == 1) {
            uint coefficientStride = specializedN * stateWidth;
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
                float2 increment = coefficients[2 * coefficientStride + index] * base[index]
                                   + (dt / 4) * (first[index] + coefficients[2 * coefficientStride + index] * first[index])
                                   + (3 * dt / 4) * (f + coefficients[index] * f);
                omega[index] = compensatedAdd(base[index], increment, remainder[index]);
            }
        } else {
            uint coefficientStride = specializedN * stateWidth;
            if (p.stage == 0) {
                float z = -p.viscosity * dt * k2;
                float3 phi = phi123(z);
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
                float2 increment = coefficients[index] * base[index] + coefficients[3 * coefficientStride + index] * first[index]
                                   + 2 * coefficients[4 * coefficientStride + index] * second[index] + coefficients[5 * coefficientStride + index] * f;
                omega[index] = compensatedAdd(base[index], increment, remainder[index]);
            }
        }
        if (!all(isfinite(omega[index]))) atomic_store_explicit(&clock.valid, 0u, memory_order_relaxed);
    }

    kernel void commitStep(device ClockState &clock [[buffer(0)]], constant Params &p [[buffer(1)]],
                           uint tid [[thread_position_in_grid]]) {
        if (tid != 0 || clock.active == 0 || atomic_load_explicit(&clock.valid, memory_order_relaxed) == 0) return;
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

    kernel void spectralStatistics(device const float2 *omega [[buffer(0)]],
                                   device float4 *partials [[buffer(1)]], constant Params &p [[buffer(2)]],
                                   uint tid [[thread_position_in_grid]], uint local [[thread_index_in_threadgroup]],
                                   uint group [[threadgroup_position_in_grid]]) {
        threadgroup float energies[256], enstrophies[256], palinstrophies[256];
        threadgroup uint invalid[256];
        float energy = 0, enstrophy = 0, palinstrophy = 0; uint bad = 0;
        uint width = specializedN / 2 + 1;
        for (uint i = tid; i < specializedN * width; i += p.groups * 256) {
            float2 w = omega[i] * p.inverseN2;
            bad |= uint(!all(isfinite(w)));
            int x = int(i % width), y = wave(i / width, specializedN);
            // Interior columns also represent their negative-kx conjugates.
            // kx=0 (and the excluded Nyquist column) already hold both ky signs.
            float weight = x == 0 || x == int(specializedN / 2) ? 1.0f : 2.0f;
            float k2 = float(x * x + y * y), power = weight * (0.5f * dot(w, w));
            enstrophy += power;
            palinstrophy += k2 * power;
            if (k2 > 0) energy += power / k2;
        }
        energies[local] = energy; enstrophies[local] = enstrophy; palinstrophies[local] = palinstrophy; invalid[local] = bad;
        threadgroup_barrier(mem_flags::mem_threadgroup);
        for (uint s = 128; s > 0; s >>= 1) {
            if (local < s) { energies[local] += energies[local + s]; enstrophies[local] += enstrophies[local + s]; palinstrophies[local] += palinstrophies[local + s]; invalid[local] |= invalid[local + s]; }
            threadgroup_barrier(mem_flags::mem_threadgroup);
        }
        if (local == 0) partials[group] = float4(energies[0], enstrophies[0], float(invalid[0]), palinstrophies[0]);
    }

    kernel void prepareSnapshot(device const float2 *omega [[buffer(0)]], device float2 *spectra [[buffer(1)]],
                                constant Params &p [[buffer(2)]], uint2 gid [[thread_position_in_grid]]) {
        uint width = specializedN / 2 + 1;
        if (gid.x >= width || gid.y >= specializedN) return;
        uint i = gid.y * width + gid.x, stride = specializedN * width;
        float2 w = omega[i];
        spectra[i] = w;
        if (p.stage == 3) {
            int x = int(gid.x), y = wave(gid.y, specializedN);
            float k2 = float(x * x + y * y);
            float2 iw = timesI(w);
            spectra[stride + i] = k2 > 0 ? iw * (float(y) / k2) : float2(0);
            spectra[2 * stride + i] = k2 > 0 ? iw * (-float(x) / k2) : float2(0);
        }
    }

    kernel void extractSnapshot(device const float *padded [[buffer(0)]], device float *fields [[buffer(1)]],
                                device float4 *partials [[buffer(2)]], constant Params &p [[buffer(3)]],
                                uint tid [[thread_position_in_grid]], uint local [[thread_index_in_threadgroup]],
                                uint group [[threadgroup_position_in_grid]]) {
        threadgroup float maxima[256], speeds[256], sums[256], squares[256];
        threadgroup uint invalid[256];
        uint count = specializedN * specializedN, row = 2 * (specializedN / 2 + 1), stride = specializedN * row;
        float maxOmega = 0, maxSpeed = 0, sumOmega = 0, sumOmegaSquared = 0; uint bad = 0;
        for (uint i = tid; i < count; i += p.groups * 256) {
            uint j = (i / specializedN) * row + i % specializedN;
            float w = padded[j] * p.inverseN2;
            float u = p.stage == 3 ? padded[stride + j] * p.inverseN2 : 0;
            float v = p.stage == 3 ? padded[2 * stride + j] * p.inverseN2 : 0;
            fields[i] = w; fields[count + i] = u; fields[2 * count + i] = v;
            maxOmega = max(maxOmega, abs(w)); maxSpeed = max(maxSpeed, length(float2(u, v)));
            sumOmega += w; sumOmegaSquared += w * w;
            bad |= uint(!isfinite(w) || !isfinite(u) || !isfinite(v));
        }
        maxima[local] = maxOmega; speeds[local] = maxSpeed; invalid[local] = bad;
        sums[local] = sumOmega; squares[local] = sumOmegaSquared;
        threadgroup_barrier(mem_flags::mem_threadgroup);
        for (uint s = 128; s > 0; s >>= 1) {
            if (local < s) {
                maxima[local] = max(maxima[local], maxima[local + s]); speeds[local] = max(speeds[local], speeds[local + s]);
                invalid[local] |= invalid[local + s];
                sums[local] += sums[local + s]; squares[local] += squares[local + s];
            }
            threadgroup_barrier(mem_flags::mem_threadgroup);
        }
        if (local == 0) {
            partials[group] = float4(maxima[0], speeds[0], float(invalid[0]), 0);
            partials[p.groups + group] = float4(sums[0], squares[0], 0, 0);
        }
    }
    """#
}
