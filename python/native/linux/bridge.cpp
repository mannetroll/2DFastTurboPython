// Linux host for the shared CUDA/VkFFT solver. Headless initialization needs only CUDA.
#include "fastturbo.h"
#include "cuda_loader.hpp"
#include "ui.hpp"
#include "../cuda/bridge_common.hpp"
#include <chrono>
#include <thread>
#include <unistd.h>
namespace {
int deviceId = 0;
}
// Small file/clock helpers adapted from reference-windows/src/main.cpp.
namespace turbo {
double nowSeconds(){return std::chrono::duration<double>(std::chrono::steady_clock::now().time_since_epoch()).count();}
std::vector<float> loadInitial(const Options& o) {
    if(o.initial.empty())return {};
    size_t count=size_t(o.config.n)*o.config.n;
    if(std::filesystem::file_size(o.initial)!=count*4)throw std::runtime_error("Initial file must contain exactly N² Float32 samples.");
    std::vector<float> data(count);std::ifstream in(o.initial,std::ios::binary);
    if(!in.read(reinterpret_cast<char*>(data.data()),std::streamsize(count*4)))throw std::runtime_error("Cannot read initial file.");
    return data;
}
void writeSpectrum(Solver& s,const Options& o) {
    if(!o.spectrum.empty())writeFile(o.spectrum,s.spectrum());
    if(!o.fields.empty()){auto f=s.snapshot(true);if(!f)throw std::runtime_error("No snapshot slot.");writeFile(o.fields,s.readFrame(*f));}
}
}


extern "C" {
void ft_string_free(char* p) { std::free(p); }
char* ft_initialize(const char* resources) {
    return response([&] {
        initializeCudaLoader();
        deviceId=requestedDevice();
        CUDA_CHECK(cudaSetDevice(deviceId));
        cudaDeviceProp prop{};
        CUDA_CHECK(cudaGetDeviceProperties(&prop, deviceId));
        initializeLinuxUI(resources,prop.name);
        int runtime=0, driver=0;
        CUDA_CHECK(cudaRuntimeGetVersion(&runtime));
        CUDA_CHECK(cudaDriverGetVersion(&driver));
        Json info{{"abi_version",1},{"defaults",configReport(Config{})},
            {"backend","CUDA / VkFFT"},{"gpu",prop.name},{"os","Linux"},
            {"gpu_memory_bytes",prop.totalGlobalMem},
            {"physical_memory_bytes",uint64_t(sysconf(_SC_PHYS_PAGES))*sysconf(_SC_PAGESIZE)},
            {"compute_capability",std::to_string(prop.major)+"."+std::to_string(prop.minor)},
            {"cuda_runtime_version",runtime},{"cuda_driver_version",driver},
            {"integrators",methods},
            {"capabilities",{{"headless",true},{"native_rendering",linuxRenderingBuilt()},
                {"forcing",false},{"drag",false},{"vortex_injection",false}}}};
        if(!linuxRenderingBuilt())info["native_rendering_error"]="GPU rendering was not built. Install OpenGL/X11/libpng development files and rebuild, or use --headless.";
        deviceMetadata(info,deviceId);
        return info;
    });
}
void* ft_engine_create(const char* input,char** error) {
    try{CUDA_CHECK(cudaSetDevice(deviceId));return new EngineHandle(decode(input));}
    catch(const std::exception& e){if(error)*error=copyString(e.what());return nullptr;}
}
void ft_engine_destroy(void* handle){if(handle){cudaSetDevice(deviceId);delete static_cast<EngineHandle*>(handle);}}
char* ft_engine_call(void* handle,const char* input) {
    return response([&]()->Json{
        if(!handle)throw std::runtime_error("Closed engine.");
        auto& h=*static_cast<EngineHandle*>(handle);std::lock_guard lock(h.mutex);CUDA_CHECK(cudaSetDevice(deviceId));
        auto j=decode(input);auto op=j.value("op","");auto& core=h.core;
        if(op=="inspect")return statsReport(core.inspect());
        if(op=="advance")return statsReport(core.advance(j.value("steps",1),has(j,"until")?std::optional<double>(scalar(j,"until",0)):std::nullopt));
        if(op=="set"){
            if(has(j,"reynolds"))core.setReynolds(scalar(j,"reynolds",0));
            if(has(j,"cfl"))core.setCFL(float(scalar(j,"cfl",0)));
            return Json::object();
        }
        if(op=="reset"){Options o;o.config=core.configuration();o.initial=path(j,"initial");core.reset(loadInitial(o));return statsReport(core.inspect());}
        if(op=="export"){
            if(has(j,"spectrum"))writeFile(path(j,"spectrum"),core.spectrum());
            if(j.value("snapshot",false)||has(j,"fields")||has(j,"png")){
                auto f=core.snapshot(j.value("include_velocity",true));if(!f)throw std::runtime_error("No snapshot slot.");
                exportFields(*f,j);
                if(has(j,"png")){
                    auto renderer=linuxRenderer();
                    renderer->exportPNG(f,displayOptions(j),path(j,"png"),pixelSize(j));
                }
                return frameReport(*f);
            }
            return Json::object();
        }
        throw std::invalid_argument("Unknown engine operation.");
    });
}

}
