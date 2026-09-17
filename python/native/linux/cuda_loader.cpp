#include "cuda_loader.hpp"
#include <cuda.h>
#include <nvrtc.h>
#include <dlfcn.h>
#include <filesystem>
#include <fstream>
#include <mutex>
#include <stdexcept>
#include <string>

void initializeCudaLoader() {
    static std::once_flag once;
    std::call_once(once, [] {
        // NVRTC dlopens its builtins by soname. The caller's RUNPATH is not
        // inherited by that lookup in a relocatable/user-local toolkit.
        // Resolve the library actually linked here and preload its matching
        // builtins, without LD_LIBRARY_PATH or changes to the system loader.
        Dl_info nvrtc{};
        int major=0, minor=0;
        if (!dladdr(reinterpret_cast<void*>(&nvrtcVersion), &nvrtc) ||
            nvrtcVersion(&major, &minor) != NVRTC_SUCCESS)
            throw std::runtime_error("Cannot locate the loaded CUDA NVRTC library.");
        auto builtins = std::filesystem::path(nvrtc.dli_fname).parent_path() /
            ("libnvrtc-builtins.so." + std::to_string(major) + "." + std::to_string(minor));
        if (!dlopen(builtins.c_str(), RTLD_NOW | RTLD_LOCAL))
            throw std::runtime_error(std::string("Cannot load matching CUDA NVRTC builtins: ") + dlerror());
        auto result = cuInit(0);
        if (result != CUDA_SUCCESS) {
            const char* error = nullptr;
            cuGetErrorString(result, &error);
            throw std::runtime_error(std::string("CUDA driver initialization: ") + (error ? error : "unknown failure"));
        }
        // The WSL libcuda proxy loads the active Windows driver's Linux UMD.
        // Discover that exact directory from loaded mappings, never guess a
        // driver version or select an arbitrary directory from the driver store.
        std::ifstream maps("/proc/self/maps");
        std::string line;
        while (std::getline(maps, line)) {
            auto begin = line.find("/usr/lib/wsl/drivers/");
            if (begin == std::string::npos || line.find("/libcuda.so", begin) == std::string::npos) continue;
            auto folder = std::filesystem::path(line.substr(begin)).parent_path();
            // A stale distro PTX JIT can otherwise be picked via ld.so.cache,
            // crashing the newer WSL UMD even when native sm_86 code is present.
            // Keep these process-local handles alive for the CUDA lifetime.
            for (auto name : {"libnvidia-gpucomp.so", "libnvidia-ptxjitcompiler.so.1"}) {
                auto library = folder / name;
                if (std::filesystem::exists(library) && !dlopen(library.c_str(), RTLD_NOW | RTLD_GLOBAL))
                    throw std::runtime_error(std::string("Cannot load WSL CUDA dependency: ") + dlerror());
            }
            break;
        }
    });
}
