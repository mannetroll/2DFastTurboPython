// Resolve the host driver at runtime. A wheel must never link or bundle the
// toolkit's libcuda stub (nor redistribute the machine's NVIDIA driver).
#include <cuda.h>
#include <dlfcn.h>
#include <stdexcept>
#include <string>

namespace {
void* driverSymbol(const char* name) {
    static void* driver = [] {
        void* handle = dlopen("libcuda.so.1", RTLD_NOW | RTLD_LOCAL);
        if (!handle)
            throw std::runtime_error(std::string("Cannot load NVIDIA driver libcuda.so.1: ") + dlerror());
        return handle; // Keep the driver loaded for the process lifetime.
    }();
    void* symbol = dlsym(driver, name);
    if (!symbol)
        throw std::runtime_error(std::string("NVIDIA driver symbol unavailable: ") + name);
    return symbol;
}
}

// Expand CUDA's versioned aliases (e.g. cuModuleGetGlobal_v2) before stringizing.
#define FT_STRING_IMPL(name) #name
#define FT_STRING(name) FT_STRING_IMPL(name)
#define FT_DRIVER(name, signature, arguments) \
    extern "C" CUresult CUDAAPI name signature { \
        static auto dispatch = reinterpret_cast<decltype(&name)>(driverSymbol(FT_STRING(name))); \
        return dispatch arguments; \
    }

FT_DRIVER(cuInit, (unsigned int flags), (flags))
FT_DRIVER(cuGetErrorString, (CUresult error, const char** text), (error, text))
FT_DRIVER(cuDeviceGet, (CUdevice* device, int ordinal), (device, ordinal))
FT_DRIVER(cuDeviceGetAttribute, (int* value, CUdevice_attribute attribute, CUdevice device), (value, attribute, device))
FT_DRIVER(cuModuleLoadDataEx, (CUmodule* module, const void* data, unsigned int count, CUjit_option* options, void** values), (module, data, count, options, values))
FT_DRIVER(cuModuleUnload, (CUmodule module), (module))
FT_DRIVER(cuModuleGetFunction, (CUfunction* function, CUmodule module, const char* name), (function, module, name))
FT_DRIVER(cuModuleGetGlobal, (CUdeviceptr* pointer, size_t* size, CUmodule module, const char* name), (pointer, size, module, name))
FT_DRIVER(cuFuncSetAttribute, (CUfunction function, CUfunction_attribute attribute, int value), (function, attribute, value))
FT_DRIVER(cuMemcpyHtoD, (CUdeviceptr destination, const void* source, size_t size), (destination, source, size))
FT_DRIVER(cuLaunchKernel, (CUfunction function, unsigned int gx, unsigned int gy, unsigned int gz,
    unsigned int bx, unsigned int by, unsigned int bz, unsigned int memory,
    CUstream stream, void** parameters, void** extra),
    (function, gx, gy, gz, bx, by, bz, memory, stream, parameters, extra))
