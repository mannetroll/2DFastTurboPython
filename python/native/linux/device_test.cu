// Minimal independent access check: enumeration, allocation, kernel, readback.
#include <cuda_runtime.h>
#include <cstdio>
__global__ void answer(int* value) { *value = 42; }
int main() {
    auto check = [](cudaError_t rc, const char* operation) {
        std::printf("%s: %d (%s)\n", operation, int(rc), cudaGetErrorString(rc));
        return rc == cudaSuccess;
    };
    int count = 0;
    if (!check(cudaGetDeviceCount(&count), "cudaGetDeviceCount")) return 1;
    std::printf("device_count: %d\n", count);
    if (!count) return 1;
    cudaDeviceProp properties{};
    if (!check(cudaGetDeviceProperties(&properties, 0), "cudaGetDeviceProperties")) return 1;
    std::printf("device: %s; compute capability: %d.%d\n", properties.name, properties.major, properties.minor);
    int* device = nullptr;
    if (!check(cudaMalloc(&device, sizeof(int)), "cudaMalloc")) return 1;
    answer<<<1, 1>>>(device);
    bool okay = check(cudaDeviceSynchronize(), "kernel + cudaDeviceSynchronize");
    int result = 0;
    okay &= check(cudaMemcpy(&result, device, sizeof(int), cudaMemcpyDeviceToHost), "cudaMemcpy");
    okay &= check(cudaFree(device), "cudaFree");
    std::printf("result: %d (expected 42)\n", result);
    return okay && result == 42 ? 0 : 1;
}
