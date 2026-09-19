#include "legacy_cnab2.hpp"
#include "cuda_support.cuh"
#include "fft.hpp"
#include <array>
#include <numbers>

namespace turbo::legacy {
namespace {
// CUDA implementation of the historical CNAB2 method specified by
// tools/CNAB2.metal. All FFT work goes through the production FFT class.
struct Parameters { int n,m,pitch; float argScale=0,c2=0,c3=0,divxz,dx=0; };
__device__ int wave(int z,int n) { return z<n/2?z:z-n; }
__global__ void prepare(const V2* input,V2* padded,Parameters p,bool initial) {
    int x=blockIdx.x*16+threadIdx.x,y=blockIdx.y*16+threadIdx.y;
    if(x>=p.pitch||y>=p.m)return;
    size_t j=size_t(y)*p.pitch+x,stride=size_t(p.m)*p.pitch;
    V2 u=0,v=0;
    // KM3 restores the compact (kx=0, kz=-N/2) coefficient. On the
    // padded grid, split it between conjugate +/-N/2 modes so C2R has
    // Hermitian input and preserves mem_cuda's observed real inverse.
    const bool cutoffPair=x==0&&(y==p.n/2||y==p.n);
    if(x<p.n/2 && (y<p.n/2||y>=p.n||cutoffPair)) {
        int z=y==p.n/2?p.n/2:(y<p.n/2?y:y-p.n/2);
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
    if(cutoffPair) {
        u=u*.5f;v=v*.5f;
        if(y==p.n/2){u.y=-u.y;v.y=-v.y;}
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
    V2 num=omega[i]*(1.f-arg);num=num+fn*p.c2;num=num+history[i]*p.c3;
    omega[i]=num/den;history[i]=fn;
}
__global__ void extract(const float* padded,float* velocity,Parameters p) {
    int x=blockIdx.x*16+threadIdx.x,y=blockIdx.y*16+threadIdx.y;
    if(x>=p.m||y>=p.m)return;
    size_t i=size_t(y)*p.m+x,j=size_t(y)*2*p.pitch+x;
    velocity[i]=padded[j];velocity[size_t(p.m)*p.m+i]=padded[size_t(p.m)*2*p.pitch+j];
}
__global__ void copySpectrum(const V2* input,V2* output,int n,int pitch,float scale,bool importing) {
    int x=blockIdx.x*16+threadIdx.x,y=blockIdx.y*16+threadIdx.y;
    if(x>=pitch||y>=n)return;
    size_t compact=size_t(y)*(n/2)+x,wide=size_t(y)*pitch+x;
    if(importing){if(x<n/2)output[compact]=input[wide]*scale;}
    else output[wide]=x<n/2?input[compact]*scale:V2(0);
}
__global__ void maximum(const float* velocity,float* partials,Parameters p) {
    __shared__ float values[256];
    float best=0;
    for(size_t i=blockIdx.x*256+threadIdx.x;i<size_t(p.m)*p.m;i+=64*256) {
        float u=velocity[i],v=velocity[size_t(p.m)*p.m+i];
        const float value=__fadd_rn(__fdiv_rn(fabsf(u),p.dx),__fdiv_rn(fabsf(v),p.dx));
        best=fmaxf(best,isfinite(u)&&isfinite(v)?value:INFINITY);
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
    Row row;double cnm1=0;float resumeRatio=0;
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
        p.dx=2.f*std::numbers::pi_v<float>/float(p.n);
        inverse();row.cflMaximum=cflMaximum();
        row.dt=c.fixedDt>0?c.fixedDt:float(c.cfl)/float(row.cflMaximum)/std::numbers::pi_v<float>;
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
        double result=double(peak);
        if(result<0||!std::isfinite(result))throw std::runtime_error("CNAB2 nonfinite CFL maximum.");
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
    return advanceTo(impl->config.steps,{},true,checkpoint);
}
Row Solver::advanceSteps(int steps,std::optional<double> until) {
    if(steps<0||steps>std::numeric_limits<int>::max()-impl->row.iteration||
       (until&&(!std::isfinite(*until)||*until<0)))throw std::invalid_argument("Invalid CNAB2 step count or endpoint.");
    return advanceTo(impl->row.iteration+steps,until,false,{});
}
Row Solver::advanceTo(int target,std::optional<double> until,bool finalCheckpoint,
                      const std::function<void(const Row&)>& checkpoint) {
    auto& s=*impl;auto& row=s.row;
    for(int i=row.iteration+1;i<=target;++i) {
        if(until&&reached(row.stateTime,*until))break;
        if(s.resumeRatio>0){s.cnm1=s.resumeRatio;s.resumeRatio=0;}
        double old=row.dt;bool clipped=until&&old>=*until-row.stateTime;
        if(clipped){old=float(*until-row.stateTime);s.cnm1=float(s.cnm1)*float(old/row.dt);}
        if(!(old>0)||!std::isfinite(old))throw std::runtime_error("CNAB2 nonfinite or zero timestep.");
        row.time=row.stateTime;s.step(old);s.cnm1=row.cn;
        row.iteration=i;row.stateTime=clipped?*until:row.stateTime+old;
        if(i==1||i%s.config.update==0||(finalCheckpoint&&i==target)) {
            row.cflMaximum=s.cflMaximum();
            const float dt=float(row.dt),cfl=float(row.cflMaximum)*dt*std::numbers::pi_v<float>;
            const float cn=s.config.fixedDt>0||cfl==0?1.f:.8f+.2f*(float(s.config.cfl)/cfl);
            row.cn=cn;row.dt=s.config.fixedDt>0?s.config.fixedDt:dt*cn;
            if(finalCheckpoint||checkpoint)row.tau=s.tau();
            if(checkpoint)checkpoint(row);
        } else if(i%128==0)CUDA_CHECK(cudaStreamSynchronize(s.stream.value));
        if(clipped)s.resumeRatio=float(row.dt/old);
    }
    CUDA_CHECK(cudaStreamSynchronize(s.stream.value));return row;
}
void Solver::setReynolds(double re) {
    if(!(re>0)||!std::isfinite(float(re))||!(float(1/float(re))>0))throw std::invalid_argument("CNAB2 Re must be finite and positive.");
    impl->config.reynolds=re;impl->init.viscosity=float(1/float(re));
}
void Solver::setCFL(float cfl) {
    if(!(cfl>0)||!std::isfinite(cfl))throw std::invalid_argument("CNAB2 CFL must be finite and positive.");
    impl->config.cfl=cfl;
}
cudaStream_t Solver::stream()const{return impl->stream.value;}
size_t Solver::fftBytes()const{return impl->fft.allocatedBytes();}
void Solver::copySpectrumTo(Complex* target,int pitch,float scale)const {
    auto& s=*impl;
    copySpectrum<<<s.grid(pitch,s.p.n),dim3(16,16),0,stream()>>>(s.omega.as<V2>(),reinterpret_cast<V2*>(target),s.p.n,pitch,scale,false);
    CUDA_CHECK(cudaGetLastError());CUDA_CHECK(cudaStreamSynchronize(stream()));
}
void Solver::loadSpectrum(const Complex* source,int pitch,float scale) {
    auto& s=*impl;
    copySpectrum<<<s.grid(pitch,s.p.n),dim3(16,16),0,stream()>>>(reinterpret_cast<const V2*>(source),s.omega.as<V2>(),s.p.n,pitch,scale,true);
    CUDA_CHECK(cudaMemsetAsync(s.history.data,0,s.history.bytes,stream()));
    prepare<<<s.grid(s.p.pitch,s.p.m),dim3(16,16),0,stream()>>>(s.omega.as<V2>(),s.padded.as<V2>(),s.p,false);
    s.inverse();s.row={};s.cnm1=0;s.resumeRatio=0;s.row.cflMaximum=s.cflMaximum();
    s.row.dt=s.config.fixedDt>0?s.config.fixedDt:float(s.config.cfl)/float(s.row.cflMaximum>0?s.row.cflMaximum:1)/std::numbers::pi_v<float>;
}
size_t Solver::allocatedBytes()const {
    auto& s=*impl;return s.omega.bytes+s.history.bytes+s.padded.bytes+s.velocity.bytes+s.partials.bytes+s.fft.allocatedBytes();
}
std::vector<Complex> Solver::spectrum()const{return impl->read<Complex>(impl->omega);}
std::vector<Complex> Solver::history()const{return impl->read<Complex>(impl->history);}
std::vector<float> Solver::velocity()const{return impl->read<float>(impl->velocity);}
}
