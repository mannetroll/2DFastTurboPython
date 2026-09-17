#include "legacy_cnab2.hpp"
#include "cuda_support.cuh"
#include "fft.hpp"
#include <array>
#include <numbers>

namespace turbo::legacy {
namespace {
// CUDA implementation of the historical CNAB2 method specified by
// tools/CNAB2.metal. All FFT work goes through the production FFT class.
struct Parameters { int n,m,pitch; float argScale=0,c2=0,c3=0,divxz; };
__device__ int wave(int z,int n) { return z<n/2?z:z-n; }
__global__ void prepare(const V2* input,V2* padded,Parameters p,bool initial) {
    int x=blockIdx.x*16+threadIdx.x,y=blockIdx.y*16+threadIdx.y;
    if(x>=p.pitch||y>=p.m)return;
    size_t j=size_t(y)*p.pitch+x,stride=size_t(p.m)*p.pitch;
    V2 u=0,v=0;
    if(x<p.n/2 && (y<p.n/2||y>=p.n)) {
        int z=y<p.n/2?y:y-p.n/2;
        size_t i=size_t(z)*(p.n/2)+x;
        if(initial){u=input[i];v=input[size_t(p.n)*(p.n/2)+i];}
        else {
            float fx=float(x),fy=float(wave(z,p.n));
            V2 om=input[i],base=om*(1.f/(fx*fx+fy*fy+1.e-30f));
            V2 by=base*fy,bx=base*fx;
            u={by.y,-by.x}; v={-bx.y,bx.x};
            if(x==0){u=V2(om.y,-om.x)*(z>=1&&fy!=0?1.f/fy:0.f);v=0;}
        }
    }
    padded[j]=u;padded[stride+j]=v;
}
__global__ void products(const float* velocity,float* padded,Parameters p) {
    int x=blockIdx.x*16+threadIdx.x,y=blockIdx.y*16+threadIdx.y;
    if(x>=p.m||y>=p.m)return;
    size_t i=size_t(y)*p.m+x,j=size_t(y)*2*p.pitch+x,stride=size_t(p.m)*2*p.pitch;
    float u=velocity[i],v=velocity[size_t(p.m)*p.m+i];
    padded[j]=u*u;padded[stride+j]=v*v;padded[2*stride+j]=u*v;
}
__global__ void advanceKernel(const V2* padded,V2* omega,V2* history,Parameters p) {
    int x=blockIdx.x*16+threadIdx.x,y=blockIdx.y*16+threadIdx.y;
    if(x>=p.n/2||y>=p.n)return;
    size_t i=size_t(y)*(p.n/2)+x;
    int z=y<p.n/2?y:y+p.n/2;
    size_t j=size_t(z)*p.pitch+x,stride=size_t(p.m)*p.pitch;
    float fx=float(x),fy=float(wave(y,p.n));
    V2 a=z==p.n?V2(0):padded[j],b=z==p.n?V2(0):padded[stride+j],c=z==p.n?V2(0):padded[2*stride+j];
    V2 fn=(a-b)*(fy*fx);fn=fn+c*(fy*fy-fx*fx);fn=fn*p.divxz;
    float arg=(fx*fx+fy*fy)*p.argScale,den=arg+1.f;
    V2 num=omega[i]-omega[i]*arg;num=num+fn*p.c2;num=num+history[i]*p.c3;
    omega[i]=num*(1.f/den);history[i]=fn;
}
__global__ void extract(const float* padded,float* velocity,Parameters p) {
    int x=blockIdx.x*16+threadIdx.x,y=blockIdx.y*16+threadIdx.y;
    if(x>=p.m||y>=p.m)return;
    size_t i=size_t(y)*p.m+x,j=size_t(y)*2*p.pitch+x;
    velocity[i]=padded[j];velocity[size_t(p.m)*p.m+i]=padded[size_t(p.m)*2*p.pitch+j];
}
__global__ void maximum(const float* velocity,float* partials,Parameters p) {
    __shared__ float values[256];
    float best=0;
    for(size_t i=blockIdx.x*256+threadIdx.x;i<size_t(p.m)*p.m;i+=64*256) {
        float u=velocity[i],v=velocity[size_t(p.m)*p.m+i];
        best=fmaxf(best,isfinite(u)&&isfinite(v)?fabsf(u)+fabsf(v):INFINITY);
    }
    values[threadIdx.x]=best;__syncthreads();
    for(int stride=128;stride>0;stride>>=1){if(threadIdx.x<stride)values[threadIdx.x]=fmaxf(values[threadIdx.x],values[threadIdx.x+stride]);__syncthreads();}
    if(threadIdx.x==0)partials[blockIdx.x]=values[0];
}
// Local ownership only; no additional numerical/FFT implementation.
struct Buffer {
    void* data=nullptr;size_t bytes;
    explicit Buffer(size_t bytes):bytes(bytes){CUDA_CHECK(cudaMalloc(&data,bytes));}
    ~Buffer(){cudaFree(data);}
    Buffer(const Buffer&)=delete;Buffer& operator=(const Buffer&)=delete;
    template<class T>T* as()const{return static_cast<T*>(data);}
};
struct Stream {
    cudaStream_t value{};
    Stream(){CUDA_CHECK(cudaStreamCreateWithFlags(&value,cudaStreamNonBlocking));}
    ~Stream(){cudaStreamSynchronize(value);cudaStreamDestroy(value);}
};
}
struct Solver::Impl {
    Config config;Initial init;Stream stream;Parameters p;
    Buffer omega,history,padded,velocity,partials;
    FFT fft;
    Row row;double cnm1=0;
    explicit Impl(const Config& c):config(c),init(initialize(c)),
        p{c.n,c.m(),c.m()/2+1,0,0,0,1.f/(float(c.m())*float(c.m()))},
        omega(init.omega.size()*8),history(omega.bytes),
        padded(size_t(3)*p.m*p.pitch*8),velocity(size_t(2)*p.m*p.m*4),partials(64*4),
        fft(p.m,true,p.pitch,0,stream.value) {
        CUDA_CHECK(cudaMemcpyAsync(omega.data,init.omega.data(),omega.bytes,cudaMemcpyHostToDevice,stream.value));
        CUDA_CHECK(cudaMemsetAsync(history.data,0,history.bytes,stream.value));
        Buffer initial(init.velocity.size()*8);
        CUDA_CHECK(cudaMemcpyAsync(initial.data,init.velocity.data(),initial.bytes,cudaMemcpyHostToDevice,stream.value));
        fft.prepare(3);fft.prepare(2);
        prepare<<<grid(p.pitch,p.m),dim3(16,16),0,stream.value>>>(initial.as<V2>(),padded.as<V2>(),p,true);
        inverse();row.cflMaximum=cflMaximum();row.dt=c.cfl/(row.cflMaximum*std::numbers::pi);
        row.tau=tau();
    }
    ~Impl(){cudaStreamSynchronize(stream.value);}
    dim3 grid(int x,int y)const{return dim3((x+15)/16,(y+15)/16);}
    void inverse(){fft.transform(padded.data,2,true);extract<<<grid(p.m,p.m),dim3(16,16),0,stream.value>>>(padded.as<float>(),velocity.as<float>(),p);}
    template<class T>std::vector<T> read(const Buffer& buffer)const {
        std::vector<T> values(buffer.bytes/sizeof(T));
        CUDA_CHECK(cudaMemcpyAsync(values.data(),buffer.data,buffer.bytes,cudaMemcpyDeviceToHost,stream.value));
        CUDA_CHECK(cudaStreamSynchronize(stream.value));return values;
    }
    double cflMaximum() {
        maximum<<<64,256,0,stream.value>>>(velocity.as<float>(),partials.as<float>(),p);
        CUDA_CHECK(cudaGetLastError());auto values=read<float>(partials);
        float peak=*std::max_element(values.begin(),values.end());
        double result=double(peak*float(double(p.n)/(2*std::numbers::pi)));
        if(!(result>0)||!std::isfinite(result))throw std::runtime_error("CNAB2 nonfinite or zero CFL maximum.");
        return result;
    }
    double tau()const {
        auto values=read<Complex>(omega);double energy=0,length=0;
        for(int z=0;z<p.n;++z)for(int x=0;x<p.n/2;++x) {
            float y=float(z<p.n/2?z:z-p.n),k2=float(x*x)+y*y;
            if(k2<=0)continue;
            auto om=values[size_t(z)*(p.n/2)+x];float power=om.x*om.x+om.y*om.y;
            power=power/k2;float weight=x==0||x==p.n/2-1?1.f:2.f,weighted=power*weight;
            float inverseK=1.f/std::sqrt(k2);energy+=double(weighted);length+=double(weighted*inverseK);
        }
        double result=(2*std::numbers::pi*length/energy)/std::sqrt(energy);
        if(!std::isfinite(result))throw std::runtime_error("CNAB2 nonfinite TAU_L.");
        return result;
    }
    void step(double dt) {
        float d=float(dt),previous=float(cnm1);
        p.argScale=(.5f*init.viscosity)*d;p.c2=(.5f*d)*(2.f+previous);p.c3=(-.5f*d)*previous;
        products<<<grid(p.m,p.m),dim3(16,16),0,stream.value>>>(velocity.as<float>(),padded.as<float>(),p);
        fft.transform(padded.data,3,false);
        advanceKernel<<<grid(p.n/2,p.n),dim3(16,16),0,stream.value>>>(padded.as<V2>(),omega.as<V2>(),history.as<V2>(),p);
        prepare<<<grid(p.pitch,p.m),dim3(16,16),0,stream.value>>>(omega.as<V2>(),padded.as<V2>(),p,false);
        inverse();CUDA_CHECK(cudaGetLastError());
    }
};
Solver::Solver(const Config& c):impl(std::make_unique<Impl>(c)){}
Solver::~Solver()=default;
const Initial& Solver::initial()const{return impl->init;}
Row Solver::initialRow()const{return impl->row;}
Row Solver::advance(const std::function<void(const Row&)>& checkpoint) {
    auto& s=*impl;auto& row=s.row;
    for(int i=row.iteration+1;i<=s.config.steps;++i) {
        double old=row.dt;row.time=row.stateTime;s.step(old);s.cnm1=row.cn;
        row.iteration=i;row.stateTime+=old;
        if(i==1||i%s.config.update==0||i==s.config.steps) {
            row.cflMaximum=s.cflMaximum();
            row.cn=.8+.2*(s.config.cfl/(row.cflMaximum*row.dt*std::numbers::pi));row.dt*=row.cn;
            row.tau=s.tau();if(checkpoint)checkpoint(row);
        } else if(i%128==0)CUDA_CHECK(cudaStreamSynchronize(s.stream.value));
    }
    CUDA_CHECK(cudaStreamSynchronize(s.stream.value));return row;
}
size_t Solver::allocatedBytes()const {
    auto& s=*impl;return s.omega.bytes+s.history.bytes+s.padded.bytes+s.velocity.bytes+s.partials.bytes+s.fft.allocatedBytes();
}
std::vector<Complex> Solver::spectrum()const{return impl->read<Complex>(impl->omega);}
std::vector<Complex> Solver::history()const{return impl->read<Complex>(impl->history);}
std::vector<float> Solver::velocity()const{return impl->read<float>(impl->velocity);}
}
