#include "fft.hpp"
#include "cuda_support.cuh"
#define VKFFT_BACKEND 1
#include <vkFFT.h>
#include <map>
namespace turbo {
struct FFT::Impl {
    struct Plan {
        VkFFTApplication app{};
        uint64_t bytes = 0;
        bool initialized = false;
        ~Plan() { if(initialized) deleteVkFFT(&app); }
    };
    int n, pitch, cutoff;
    bool real;
    CUdevice device{};
    cudaStream_t stream;
    std::map<int,std::unique_ptr<Plan>> plans;
    Plan& plan(int batches) {
        if (auto it=plans.find(batches);it!=plans.end()) return *it->second;
        auto result=std::make_unique<Plan>();
        result->bytes=uint64_t(pitch)*n*batches*8;
        VkFFTConfiguration c{};
        c.FFTdim=2; c.size[0]=n; c.size[1]=n; c.numberBatches=batches;
        c.device=&device; c.stream=&stream; c.num_streams=1;
        c.bufferSize=&result->bytes; c.normalize=0; c.performR2C=real;
        c.bufferStride[0]=pitch; c.bufferStride[1]=uint64_t(pitch)*n;
        if(real && n>=768) c.useLUT=1;
        if(cutoff) for(int axis=0;axis<2;++axis) {
            c.frequencyZeroPadding=1; c.performZeropadding[axis]=1;
            c.fft_zeropad_left[axis]=cutoff; c.fft_zeropad_right[axis]=n-cutoff+1;
        }
        auto code=initializeVkFFT(&result->app,c);
        if(code!=VKFFT_SUCCESS) throw std::runtime_error("VkFFT CUDA plan N="+std::to_string(n)+" batch="+std::to_string(batches)+" failed: "+std::to_string(code));
        result->initialized=true;
        auto& ref=*result; plans.emplace(batches,std::move(result)); return ref;
    }
};
FFT::FFT(int n,bool real,int pitch,int cutoff,cudaStream_t stream):impl(std::make_unique<Impl>()) {
    impl->n=n;impl->real=real;impl->pitch=pitch;impl->cutoff=cutoff;impl->stream=stream;
    int dev=0; CUDA_CHECK(cudaGetDevice(&dev));
    if(cuDeviceGet(&impl->device,dev)!=CUDA_SUCCESS) throw std::runtime_error("Cannot select CUDA driver device.");
}
FFT::~FFT()=default;
void FFT::prepare(int batches){impl->plan(batches);}
size_t FFT::allocatedBytes() const {
    std::map<void*,size_t> buffers;
    auto add=[&](void* p,size_t bytes){if(p)buffers[p]=std::max(buffers[p],bytes);};
    auto axis=[&](const VkFFTAxis& a){add(a.bufferLUT,a.bufferLUTSize);add(a.bufferRaderUintLUT,a.bufferRaderUintLUTSize);};
    for(const auto& [batch,plan]:impl->plans){const auto& app=plan->app;const auto& c=app.configuration;
        if(c.tempBuffer&&c.tempBufferSize)for(pfUINT i=0;i<c.tempBufferNum;++i)add(c.tempBuffer[i],c.tempBufferSize[i]);
        for(auto p:{app.localFFTPlan,app.localFFTPlan_inverse})if(p){axis(p->R2Cdecomposition);for(int i=0;i<VKFFT_MAX_FFT_DIMENSIONS;++i)for(int j=0;j<4;++j){axis(p->axes[i][j]);axis(p->inverseBluesteinAxes[i][j]);}}
        for(int i=0;i<VKFFT_MAX_FFT_DIMENSIONS;++i){add(app.bufferBluestein[i],app.bufferBluesteinSize[i]);add(app.bufferBluesteinFFT[i],app.bufferBluesteinSize[i]);add(app.bufferBluesteinIFFT[i],app.bufferBluesteinSize[i]);for(int j=0;j<4;++j)add(app.bufferRaderUintLUT[i][j],app.bufferRaderUintLUTSize[i][j]);}
    }
    size_t result=0;for(auto [buffer,bytes]:buffers)result+=bytes;return result;
}
void FFT::transform(void* buffer,int batches,bool inverse) {
    VkFFTLaunchParams launch{}; launch.buffer=&buffer;
    auto code=VkFFTAppend(&impl->plan(batches).app,inverse?1:-1,&launch);
    if(code!=VKFFT_SUCCESS) throw std::runtime_error("VkFFT CUDA append failed: "+std::to_string(code));
}
}
