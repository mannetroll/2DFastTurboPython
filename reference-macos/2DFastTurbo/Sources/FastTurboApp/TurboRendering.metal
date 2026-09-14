#include <metal_stdlib>
using namespace metal;

constant bool outputHDR [[function_constant(0)]];

float3 outputColor(float3 srgb) {
    if (!outputHDR) return srgb;
    // Preserve the existing palette's sRGB appearance before extending highlights.
    float3 linear = select(pow((srgb + 0.055f) / 1.055f, float3(2.4f)), srgb / 12.92f, srgb <= 0.04045f);
    float peak = max(linear.x, max(linear.y, linear.z));
    return linear * (1.0f + smoothstep(0.25f, 1.0f, peak));
}

struct TurboUniforms {
    uint gridSize;
    uint palette;
    uint display;
    uint showFlowLines;
    float exposure;
    float vorticityScale;
    float speedScale;
    float vorticityMean;
    uint vorticityColorRange;
};

struct FieldVertex {
    float4 position [[position]];
    float2 uv;
};

vertex FieldVertex turboFieldVertex(uint id [[vertex_id]]) {
    float2 p = float2((id << 1) & 2, id & 2);
    FieldVertex out;
    out.position = float4(p * 2.0 - 1.0, 0.0, 1.0);
    out.uv = p;
    return out;
}

// Samples wrap across the periodic domain. The immutable GPU frame owns
// vorticity, horizontal velocity, and vertical velocity planes.
float samplePlane(device const float *field, float2 uv, uint n, uint plane) {
    float2 p = fract(uv) * float(n);
    uint2 a = uint2(floor(p));
    uint2 b = (a + 1) % n;
    float2 f = fract(p);
    uint offset = plane * n * n;
    float lo = mix(field[offset + a.y * n + a.x], field[offset + a.y * n + b.x], f.x);
    float hi = mix(field[offset + b.y * n + a.x], field[offset + b.y * n + b.x], f.x);
    return mix(lo, hi, f.y);
}

float2 sampleVelocity(device const float *field, float2 uv, uint n) {
    return float2(samplePlane(field, uv, n, 1), samplePlane(field, uv, n, 2));
}

// Perceptually ordered Inferno anchors, interpolated entirely on the GPU.
float3 infernoColor(float t) {
    const float3 colors[6] = {
        float3(0.001, 0.000, 0.014), float3(0.258, 0.039, 0.406),
        float3(0.578, 0.148, 0.404), float3(0.865, 0.317, 0.226),
        float3(0.988, 0.645, 0.040), float3(0.988, 0.998, 0.645)
    };
    float p = saturate(t) * 5.0;
    uint i = min(uint(p), 4u);
    return mix(colors[i], colors[i + 1], p - float(i));
}

float3 signedColor(float value, uint palette) {
    if (palette == 3) return infernoColor(0.5 + 0.5 * clamp(value, -1.0, 1.0));
    float amount = saturate(abs(value));
    bool positive = value >= 0.0;
    float3 base = float3(0.017, 0.026, 0.055);
    float3 middle, bright;
    if (palette == 1) {
        middle = positive ? float3(0.92, 0.19, 0.065) : float3(0.28, 0.17, 0.69);
        bright = positive ? float3(1.0, 0.88, 0.43) : float3(0.76, 0.64, 1.0);
    } else if (palette == 2) {
        middle = positive ? float3(0.01, 0.46, 0.78) : float3(0.14, 0.24, 0.68);
        bright = positive ? float3(0.70, 1.0, 0.99) : float3(0.70, 0.79, 1.0);
    } else {
        middle = positive ? float3(0.025, 0.60, 0.58) : float3(0.48, 0.20, 0.79);
        bright = positive ? float3(0.48, 1.0, 0.84) : float3(0.94, 0.67, 1.0);
    }
    float3 color = mix(base, middle, smoothstep(0.0, 0.60, amount));
    color = mix(color, bright, smoothstep(0.34, 1.0, amount));
    return color;
}

float3 speedColor(float value, uint palette) {
    if (palette == 3) return infernoColor(value);
    float amount = saturate(value);
    float3 low = float3(0.019, 0.028, 0.063);
    float3 mid = palette == 1 ? float3(0.67, 0.10, 0.17)
        : palette == 2 ? float3(0.02, 0.33, 0.72) : float3(0.30, 0.17, 0.63);
    float3 high = palette == 1 ? float3(1.0, 0.86, 0.41)
        : palette == 2 ? float3(0.73, 0.97, 1.0) : float3(0.36, 1.0, 0.80);
    float3 color = mix(low, mid, smoothstep(0.0, 0.50, amount));
    return mix(color, high, smoothstep(0.32, 1.0, amount));
}

fragment float4 turboFieldFragment(FieldVertex in [[stage_in]],
                                    device const float *field [[buffer(0)]],
                                    constant TurboUniforms &u [[buffer(1)]]) {
    float3 color;
    if (u.display == 0) {
        float omega = samplePlane(field, in.uv, u.gridSize, 0);
        // Sigma clips around the mean; Exposure retains the original soft contrast.
        float value = u.vorticityColorRange == 0
            ? clamp((omega - u.vorticityMean) / u.vorticityScale, -1.0, 1.0)
            : tanh(omega / u.vorticityScale * max(u.exposure, 0.02) * 2.4);
        color = signedColor(value, u.palette);
    } else if (u.display == 1) {
        float speed = length(sampleVelocity(field, in.uv, u.gridSize));
        float value = 1.0 - exp(-speed / u.speedScale * max(u.exposure, 0.02) * 2.1);
        color = speedColor(value, u.palette);
    } else {
        float omega = samplePlane(field, in.uv, u.gridSize, 0) / u.vorticityScale;
        float value = 1.0 - exp(-omega * omega * max(u.exposure, 0.02) * 8.0);
        color = speedColor(value, u.palette);
    }

    return float4(outputColor(color), 1.0);
}

struct FlowVertex {
    float4 position [[position]];
    float opacity;
};

float seedHash(float2 value) {
    return fract(sin(dot(value, float2(127.1, 311.7))) * 43758.5453);
}

// Each short streamline is traced through the actual instantaneous velocity,
// with midpoint integration. Geometry scales with the seed count, not pixels.
vertex FlowVertex turboFlowVertex(uint id [[vertex_id]],
                                  device const float *field [[buffer(0)]],
                                  constant TurboUniforms &u [[buffer(1)]]) {
    const uint segments = 18;
    uint seed = id / (segments * 2);
    uint endpoint = (id % (segments * 2)) / 2 + (id & 1);
    float2 cell = float2(seed % 32, seed / 32);
    float2 jitter = float2(seedHash(cell), seedHash(cell + 39.4));
    float2 origin = (cell + 0.25 + 0.5 * jitter) / 32.0;
    float2 p = origin;
    int steps = int(endpoint) - 9;
    float dt = (steps < 0 ? -1.0 : 1.0) * 0.010 / max(u.speedScale, 0.0001);
    for (int i = 0; i < abs(steps); ++i) {
        float2 v0 = sampleVelocity(field, p, u.gridSize);
        float2 midpoint = p + 0.5 * dt * v0;
        p += dt * sampleVelocity(field, midpoint, u.gridSize);
    }
    float speed = length(sampleVelocity(field, p, u.gridSize)) / max(u.speedScale, 0.0001);
    float taper = pow(max(sin(float(endpoint) / float(segments) * M_PI_F), 0.0), 0.7);
    FlowVertex out;
    // Keep geometry continuous at boundaries; the viewport clips excess length.
    out.position = float4(p * 2.0 - 1.0, 0.0, 1.0);
    out.opacity = taper * smoothstep(0.005, 0.15, speed) * 0.25;
    return out;
}

fragment float4 turboFlowFragment(FlowVertex in [[stage_in]]) {
    return float4(outputColor(float3(0.76, 0.93, 1.0)), in.opacity);
}
