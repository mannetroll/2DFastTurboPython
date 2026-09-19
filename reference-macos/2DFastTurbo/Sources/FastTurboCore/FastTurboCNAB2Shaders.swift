// Shared by the native CNAB2 command and application.
enum FastTurboCNAB2Shaders {
    static let source = #"""
#include <metal_stdlib>
using namespace metal;
#pragma clang fp contract(off)

// Palinstrophy's compact state is [z,kx], with kx=0..N/2-1.
struct Params {
    uint n, m, pitch, groups;
    float argScale, c2, c3, divxz, dx;
};
inline int wave(uint z, uint n) { return z < n / 2 ? int(z) : int(z) - int(n); }
inline float2 minusI(float2 a) { return float2(a.y, -a.x); }
inline float2 plusI(float2 a) { return float2(-a.y, a.x); }

kernel void prepareInitial(device const float2 *initial [[buffer(0)]],
                           device float2 *padded [[buffer(1)]], constant Params &p [[buffer(2)]],
                           uint2 gid [[thread_position_in_grid]]) {
    if (gid.x >= p.pitch || gid.y >= p.m) return;
    uint j = gid.y * p.pitch + gid.x, stride = p.m * p.pitch;
    float2 u = 0, v = 0;
    // The compact KM3 cutoff is unpaired. Split it onto conjugate padded modes.
    bool cutoffPair = gid.x == 0 && (gid.y == p.n / 2 || gid.y == p.n);
    if (gid.x < p.n / 2 && (gid.y < p.n / 2 || gid.y >= p.n || cutoffPair)) {
        uint z = gid.y == p.n / 2 ? p.n / 2 : (gid.y < p.n / 2 ? gid.y : gid.y - p.n / 2);
        uint i = z * (p.n / 2) + gid.x;
        u = initial[i]; v = initial[p.n * (p.n / 2) + i];
    }
    if (cutoffPair) {
        u *= 0.5f; v *= 0.5f;
        if (gid.y == p.n / 2) { u.y = -u.y; v.y = -v.y; }
    }
    padded[j] = u; padded[stride + j] = v;
}

kernel void products(device const float *velocity [[buffer(0)]],
                     device float *padded [[buffer(1)]], constant Params &p [[buffer(2)]],
                     uint2 gid [[thread_position_in_grid]]) {
    if (gid.x >= p.m || gid.y >= p.m) return;
    uint i = gid.y * p.m + gid.x, j = gid.y * (2 * p.pitch) + gid.x;
    uint stride = p.m * (2 * p.pitch);
    float u = velocity[i], v = velocity[p.m * p.m + i];
    padded[j] = u * u;
    padded[stride + j] = v * v;
    padded[2 * stride + j] = u * v;
}

kernel void advanceCNAB2(device const float2 *padded [[buffer(0)]],
                         device float2 *omega [[buffer(1)]], device float2 *history [[buffer(2)]],
                         constant Params &p [[buffer(3)]], uint2 gid [[thread_position_in_grid]]) {
    if (gid.x >= p.n / 2 || gid.y >= p.n) return;
    uint i = gid.y * (p.n / 2) + gid.x;
    uint z = gid.y < p.n / 2 ? gid.y : gid.y + p.n / 2;
    uint j = z * p.pitch + gid.x, stride = p.m * p.pitch;
    float x = float(gid.x), y = float(wave(gid.y, p.n));
    // STEP2B zeros the z=N row before the nonlinear gather.
    float2 a = z == p.n ? float2(0) : padded[j];
    float2 b = z == p.n ? float2(0) : padded[stride + j];
    float2 c = z == p.n ? float2(0) : padded[2 * stride + j];
    float2 fn = (a - b) * (y * x);
    fn = fn + c * (y * y - x * x);
    fn = fn * p.divxz;
    float arg = (x * x + y * y) * p.argScale;
    float den = arg + 1.0f;
    float2 num = omega[i] * (1.0f - arg);
    num = num + fn * p.c2;
    num = num + history[i] * p.c3;
    omega[i] = num / den;
    history[i] = fn;
}

kernel void prepareInverse(device const float2 *omega [[buffer(0)]],
                           device float2 *padded [[buffer(1)]], constant Params &p [[buffer(2)]],
                           uint2 gid [[thread_position_in_grid]]) {
    if (gid.x >= p.pitch || gid.y >= p.m) return;
    uint j = gid.y * p.pitch + gid.x, stride = p.m * p.pitch;
    float2 u = 0, v = 0;
    // The compact KM3 cutoff is unpaired. Split it onto conjugate padded modes.
    bool cutoffPair = gid.x == 0 && (gid.y == p.n / 2 || gid.y == p.n);
    if (gid.x < p.n / 2 && (gid.y < p.n / 2 || gid.y >= p.n || cutoffPair)) {
        uint z = gid.y == p.n / 2 ? p.n / 2 : (gid.y < p.n / 2 ? gid.y : gid.y - p.n / 2);
        float x = float(gid.x), y = float(wave(z, p.n));
        float2 om = omega[z * (p.n / 2) + gid.x];
        float2 base = om * (1.0f / (x * x + y * y + 1.0e-30f));
        u = minusI(base * y); v = plusI(base * x);
        if (gid.x == 0) {
            u = minusI(om) * (z >= 1 && y != 0 ? 1.0f / y : 0.0f);
            v = 0;
        }
    }
    if (cutoffPair) {
        u *= 0.5f; v *= 0.5f;
        if (gid.y == p.n / 2) { u.y = -u.y; v.y = -v.y; }
    }
    padded[j] = u; padded[stride + j] = v;
}

kernel void extractVelocity(device const float *padded [[buffer(0)]],
                            device float *velocity [[buffer(1)]], constant Params &p [[buffer(2)]],
                            uint2 gid [[thread_position_in_grid]]) {
    if (gid.x >= p.m || gid.y >= p.m) return;
    uint i = gid.y * p.m + gid.x, j = gid.y * (2 * p.pitch) + gid.x;
    velocity[i] = padded[j];
    velocity[p.m * p.m + i] = padded[p.m * (2 * p.pitch) + j];
}

kernel void maximumVelocity(device const float *velocity [[buffer(0)]],
                            device float *partials [[buffer(1)]], constant Params &p [[buffer(2)]],
                            uint tid [[thread_position_in_grid]], uint local [[thread_index_in_threadgroup]],
                            uint group [[threadgroup_position_in_grid]]) {
    threadgroup float values[256];
    float best = 0;
    for (uint i = tid; i < p.m * p.m; i += p.groups * 256) {
        float u = velocity[i], v = velocity[p.m * p.m + i];
        float speed = isfinite(u) && isfinite(v) ? abs(u) / p.dx + abs(v) / p.dx : INFINITY;
        best = max(best, speed);
    }
    values[local] = best;
    threadgroup_barrier(mem_flags::mem_threadgroup);
    for (uint stride = 128; stride > 0; stride >>= 1) {
        if (local < stride) values[local] = max(values[local], values[local + stride]);
        threadgroup_barrier(mem_flags::mem_threadgroup);
    }
    if (local == 0) partials[group] = values[0];
}

kernel void copySpectrum(device const float2 *input [[buffer(0)]], device float2 *output [[buffer(1)]],
                         constant uint &n [[buffer(2)]], constant float &scale [[buffer(3)]],
                         constant uint &importing [[buffer(4)]], uint2 gid [[thread_position_in_grid]]) {
    uint width = n / 2 + 1;
    if (gid.x >= width || gid.y >= n) return;
    uint compact = gid.y * (n / 2) + gid.x, wide = gid.y * width + gid.x;
    if (importing) { if (gid.x < n / 2) output[compact] = input[wide] * scale; }
    else output[wide] = gid.x < n / 2 ? input[compact] * scale : float2(0);
}
"""#
}
