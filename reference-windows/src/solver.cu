#include "solver.hpp"
#include "fft.hpp"
#include "kernels.cuh"
#include "km3.hpp"
#include <array>
#include <numbers>
#include <utility>

namespace turbo {
namespace {
struct Buffer {
    void* ptr = nullptr;
    size_t bytes = 0;
    Buffer()=default;
    explicit Buffer(size_t size):bytes(size) { CUDA_CHECK(cudaMalloc(&ptr,bytes)); }
    ~Buffer(){if(ptr) cudaFree(ptr);}
    Buffer(const Buffer&)=delete;
    Buffer& operator=(const Buffer&)=delete;
    Buffer(Buffer&& b) noexcept:ptr(std::exchange(b.ptr,nullptr)),bytes(b.bytes){}
    Buffer& operator=(Buffer&& b) noexcept {if(ptr)cudaFree(ptr);ptr=std::exchange(b.ptr,nullptr);bytes=b.bytes;return *this;}
    template<class T> T* as(){return static_cast<T*>(ptr);}
};
struct Stream {
    cudaStream_t value{};
    Stream(){CUDA_CHECK(cudaStreamCreateWithFlags(&value,cudaStreamNonBlocking));}
    ~Stream(){cudaStreamSynchronize(value);cudaStreamDestroy(value);}
};
struct Events {
    cudaEvent_t begin{},end{};
    Events(){CUDA_CHECK(cudaEventCreate(&begin));CUDA_CHECK(cudaEventCreate(&end));}
    ~Events(){cudaEventDestroy(begin);cudaEventDestroy(end);}
};
// Pageable readbacks can hold CUDA's internal locks while waiting for the
// solver stream, starving the GUI's interop calls. Keep recurring readbacks
// pinned so copies enqueue promptly; synchronize before accessing the results.
struct Readback {
    struct Data {
        ClockState clock;
        std::array<float4,64> statistics;
        std::array<float4,128> snapshot;
    };
    Data* data=nullptr;
    Readback(){CUDA_CHECK(cudaMallocHost(reinterpret_cast<void**>(&data),sizeof(Data)));}
    ~Readback(){if(data)cudaFreeHost(data);}
    Readback(const Readback&)=delete;
    Readback& operator=(const Readback&)=delete;
};
std::vector<V2> nativeSpectrum(const Config& c) {
    int n=c.n,half=n/2,width=half+1;
    std::vector<V2> state(size_t(n)*width);
    uint64_t random=c.seed+0x9e3779b97f4a7c15ULL;
    auto next=[&]{random+=0x9e3779b97f4a7c15ULL; auto v=random;
        v=(v^(v>>30))*0xbf58476d1ce4e5b9ULL;v=(v^(v>>27))*0x94d049bb133111ebULL;v^=v>>31;
        return float(v>>40)/float(1<<24);};
    float k0=float(c.k0),normalization=std::numbers::pi_v<float>*k0*k0,scale=float(n*n);
    for(int iy=0;iy<n;++iy) {
        int ky=iy<=half?iy:iy-n;
        if(std::abs(ky)>=half)continue;
        for(int kx=0;kx<half;++kx) {
            if(kx==0 && ky<=0)continue;
            float k2=float(kx*kx+ky*ky),amplitude=std::exp(-k2/(k0*k0))/normalization;
            float phase=2*std::numbers::pi_v<float>*next();
            V2 direction(std::cos(phase),std::sin(phase)),u,v;
            if(kx==0){u=direction*std::sqrt(amplitude);v=0;}
            else {float ratio=float(ky)/float(kx);v=direction*std::sqrt(amplitude/(1+ratio*ratio));u=v*(-ratio);}
            V2 curl=v*float(kx)-u*float(ky),w=V2(-curl.y,curl.x)*scale;
            int sy=ky>=0?ky:ky+n;state[sy*width+kx]=w;
            if(kx==0)state[((n-sy)%n)*width]=V2(w.x,-w.y);
        }
    }
    if(c.startSpectrum==StartSpectrum::km3) {
        reshapeKM3(n,c.k0,c.seed,[&](int x,int z) {
            const double y=z<half?z:z-n,k2=double(x)*x+y*y;
            const auto w=state[size_t(z)*width+x];
            return (double(w.x)*w.x+double(w.y)*w.y)/(double(scale)*scale*k2);
        },[&](int x,int z,float ur,float ui,float vr,float vi) {
            // The production solver excludes both Nyquist axes.
            const float y=float(z<half?z:z-n);
            state[size_t(z)*width+x]=z==half?V2(0):
                V2(-float(x)*vi+y*ui,float(x)*vr-y*ur)*scale;
        });
    }
    return state;
}
}
struct Solver::Impl {
    Config config;
    Stream queue;
    Events events;
    std::unique_ptr<FFT> paddedFFT,snapshotFFT,inputFFT;
    Buffer omega,correction,padded,base,first,second,aux,coefficients,clock,cflPartials,statPartials,snapshotSpectra,snapshotPartials;
    std::array<std::shared_ptr<void>,3> slots;
    Readback readback;
    Statistics stats;
    double re;
    float cfl;
    int n,m,width,pitch;
    bool failed=false;
    Params params(int stage=0,std::optional<double> until={}) const {
        float high=float(until.value_or(0));
        return {uint(n),uint(m),uint(config.integrator),uint(stage),float(1/re),cfl,config.fixedDt,1/float(n*n),high,float(until.value_or(0)-double(high)),uint(until.has_value()),64,uint(config.form)};
    }
    explicit Impl(Config c):config(c),re(c.reynolds),cfl(c.cfl),n(c.n),m(c.m()),width(n/2+1),pitch(((m/2+1+7)/8)*8) {
        size_t stateBytes=size_t(n)*width*8;
        auto alloc=[&](size_t bytes){stats.allocatedBytes+=bytes;return Buffer(bytes);};
        omega=alloc(stateBytes);correction=alloc(stateBytes);first=alloc(stateBytes);
        padded=alloc(size_t(config.form==Form::stress?2:4)*m*pitch*8);
        base=alloc(c.integrator==Integrator::imexRK3?8:stateBytes);
        second=alloc(c.integrator==Integrator::etdrk4?stateBytes:8);
        aux=alloc(c.integrator==Integrator::etdrk4?stateBytes:8);
        int nc=c.integrator==Integrator::etdrk4?6:c.integrator==Integrator::ifHeunRK3?3:0;
        coefficients=alloc(std::max(size_t(4),size_t(nc)*n*width*4));
        clock=alloc(sizeof(ClockState));cflPartials=alloc(64*sizeof(V2));statPartials=alloc(64*sizeof(float4));
        snapshotSpectra=alloc(3*stateBytes);snapshotPartials=alloc(128*sizeof(float4));
        for(auto& slot:slots){auto b=alloc(size_t(3)*n*n*4);slot=std::shared_ptr<void>(std::exchange(b.ptr,nullptr),[](void* p){cudaFree(p);});}
        paddedFFT=std::make_unique<FFT>(m,true,pitch,n/2,queue.value);
        snapshotFFT=std::make_unique<FFT>(n,true,width,0,queue.value);
        paddedFFT->prepare(config.form==Form::stress?2:4);
        if(config.form==Form::advective)paddedFFT->prepare(1);
        snapshotFFT->prepare(1);snapshotFFT->prepare(3);
    }
    ~Impl(){cudaStreamSynchronize(queue.value);}
    void healthy() const {if(failed)throw std::runtime_error("The flow became non-finite or a GPU operation failed. Reset to recover.");}
    dim3 grid(int w,int h) const{return dim3((w+15)/16,(h+15)/16);}
    void velocity(Params p) {
        velocityToPadded<<<grid(m/2+1,m),dim3(16,16),0,queue.value>>>(omega.as<V2>(),padded.as<V2>(),p);
        paddedFFT->transform(padded.ptr,config.form==Form::stress?2:4,true);
    }
    void select(Params p) {
        reduceCFLAndProducts<<<64,256,0,queue.value>>>(padded.as<float>(),cflPartials.as<V2>(),p);
        selectTimeStep<<<1,1,0,queue.value>>>(cflPartials.as<V2>(),clock.as<ClockState>(),p);
    }
    void step(std::optional<double> until) {
        for(int stage=0;stage<(config.integrator==Integrator::etdrk4?4:3);++stage) {
            auto p=params(stage,until);velocity(p);
            if(stage==0)select(p);
            else nonlinearProducts<<<grid(m,m),dim3(16,16),0,queue.value>>>(padded.as<float>(),p);
            paddedFFT->transform(padded.ptr,config.form==Form::stress?2:1,false);
            advanceStage<<<grid(width,n),dim3(16,16),0,queue.value>>>(omega.as<V2>(),padded.as<V2>(),base.as<V2>(),first.as<V2>(),second.as<V2>(),aux.as<V2>(),coefficients.as<float>(),clock.as<ClockState>(),correction.as<V2>(),p);
        }
        commitStep<<<1,1,0,queue.value>>>(clock.as<ClockState>(),params(0,until));
    }
    void readClock() {
        CUDA_CHECK(cudaGetLastError());
        auto& hostClock=readback.data->clock;
        CUDA_CHECK(cudaMemcpyAsync(&hostClock,clock.ptr,sizeof(hostClock),cudaMemcpyDeviceToHost,queue.value));
        CUDA_CHECK(cudaStreamSynchronize(queue.value));
        if(!hostClock.valid)throw std::runtime_error("The solver encountered a non-finite field or timestep.");
        stats.time=double(hostClock.timeHigh)+hostClock.timeLow;stats.step=hostClock.steps;stats.dt=hostClock.dt;stats.reynolds=re;
        if(!std::isfinite(stats.time)||!std::isfinite(stats.dt))throw std::runtime_error("Non-finite clock.");
    }
};
Solver::Solver(Config c,const std::vector<float>& field) {
    c.validate();impl=std::make_unique<Impl>(c);reset(field);
}
Solver::~Solver()=default;
const Config& Solver::configuration() const{return impl->config;}
cudaStream_t Solver::stream()const{return impl->queue.value;}
Statistics Solver::statistics()const{impl->healthy();auto s=impl->stats;s.reynolds=impl->re;return s;}
void Solver::setReynolds(double re){if(!(re>0)||!std::isfinite(re)||!std::isfinite(float(1/re)))throw std::invalid_argument("Re must be finite and positive.");impl->re=re;}
void Solver::setCFL(float cfl){if(!(cfl>0)||!std::isfinite(cfl))throw std::invalid_argument("CFL must be finite and positive.");impl->cfl=cfl;}
void Solver::reset(const std::vector<float>& field) {
    auto& s=*impl;
    if(!field.empty()&&(field.size()!=size_t(s.n)*s.n||!std::all_of(field.begin(),field.end(),[](float x){return std::isfinite(x);})))
        throw std::invalid_argument("Initial vorticity must contain exactly N² finite Float32 samples.");
    s.failed=false;
    try {
        if(field.empty()) {
            auto values=nativeSpectrum(s.config);
            CUDA_CHECK(cudaMemcpyAsync(s.omega.ptr,values.data(),s.omega.bytes,cudaMemcpyHostToDevice,stream()));
            CUDA_CHECK(cudaStreamSynchronize(stream()));
        } else {
            std::vector<V2> values(field.size());for(size_t i=0;i<field.size();++i)values[i]=V2(field[i],0);
            Buffer input(values.size()*sizeof(V2));
            CUDA_CHECK(cudaMemcpyAsync(input.ptr,values.data(),input.bytes,cudaMemcpyHostToDevice,stream()));
            if(!s.inputFFT)s.inputFFT=std::make_unique<FFT>(s.n,false,s.n,0,stream());
            s.inputFFT->transform(input.ptr,1,false);
            projectState<<<s.grid(s.width,s.n),dim3(16,16),0,stream()>>>(input.as<V2>(),s.omega.as<V2>(),s.params());
            CUDA_CHECK(cudaStreamSynchronize(stream()));
        }
        ClockState clock{};clock.valid=1;
        CUDA_CHECK(cudaMemcpyAsync(s.clock.ptr,&clock,sizeof(clock),cudaMemcpyHostToDevice,stream()));
        CUDA_CHECK(cudaMemsetAsync(s.correction.ptr,0,s.correction.bytes,stream()));
        CUDA_CHECK(cudaMemsetAsync(s.first.ptr,0,s.first.bytes,stream()));
        s.stats.gpuMilliseconds=0;s.stats.turnover=0;
        s.velocity(s.params());s.select(s.params());s.readClock();
        auto initial=inspect();
        if(initial.energy>0&&initial.enstrophy>0)s.stats.turnover=2*std::numbers::pi*std::sqrt(initial.energy/initial.enstrophy)/std::sqrt(2*initial.energy);
    }catch(...){s.failed=true;throw;}
}
Statistics Solver::advance(int steps,std::optional<double> until) {
    auto& s=*impl;
    if(steps<0 || (until&&(!std::isfinite(*until)||*until<0||!std::isfinite(float(*until)))))throw std::invalid_argument("Invalid steps or endpoint.");
    s.healthy();if(steps==0||(until&&reached(s.stats.time,*until)))return statistics();
    try {
        float total=0;
        while(steps>0) {
            int count=std::min(steps,256);
            CUDA_CHECK(cudaEventRecord(s.events.begin,stream()));
            for(int i=0;i<count;++i)s.step(until);
            CUDA_CHECK(cudaEventRecord(s.events.end,stream()));s.readClock();
            float ms=0;CUDA_CHECK(cudaEventElapsedTime(&ms,s.events.begin,s.events.end));total+=ms;
            steps-=count;if(until&&reached(s.stats.time,*until))break;
        }
        s.stats.gpuMilliseconds=total;return statistics();
    }catch(...){s.failed=true;throw;}
}
Statistics Solver::inspect() {
    auto& s=*impl;s.healthy();
    try {
        spectralStatistics<<<64,256,0,stream()>>>(s.omega.as<V2>(),s.statPartials.as<float4>(),s.params());
        auto& partials=s.readback.data->statistics;
        CUDA_CHECK(cudaMemcpyAsync(partials.data(),s.statPartials.ptr,sizeof(partials),cudaMemcpyDeviceToHost,stream()));
        s.readClock();s.stats.energy=s.stats.enstrophy=s.stats.palinstrophy=0;
        for(auto p:partials) {
            if(p.z!=0)throw std::runtime_error("Non-finite spectral diagnostics.");
            s.stats.energy+=p.x;s.stats.enstrophy+=p.y;s.stats.palinstrophy+=p.w;
        }
        if(!std::isfinite(s.stats.energy)||!std::isfinite(s.stats.enstrophy)||!std::isfinite(s.stats.palinstrophy))throw std::runtime_error("Non-finite spectral reduction.");
        double kmax=double(s.n/2-1);s.stats.metric=s.stats.enstrophy>0?s.stats.palinstrophy/(s.stats.enstrophy*2*kmax*kmax):0;
        s.stats.fftBytes=s.paddedFFT->allocatedBytes()+s.snapshotFFT->allocatedBytes()+(s.inputFFT?s.inputFFT->allocatedBytes():0);
        return statistics();
    }catch(...){s.failed=true;throw;}
}
std::shared_ptr<Frame> Solver::snapshot(bool velocity) {
    auto& s=*impl;s.healthy();
    for(auto& slot:s.slots)if(slot.use_count()==1) {
        auto frame=std::make_shared<Frame>();frame->storage=slot;
        try {
            int fields=velocity?3:1;auto p=s.params(fields);
            prepareSnapshot<<<s.grid(s.width,s.n),dim3(16,16),0,stream()>>>(s.omega.as<V2>(),s.snapshotSpectra.as<V2>(),p);
            s.snapshotFFT->transform(s.snapshotSpectra.ptr,fields,true);
            extractSnapshot<<<64,256,0,stream()>>>(s.snapshotSpectra.as<float>(),static_cast<float*>(slot.get()),s.snapshotPartials.as<float4>(),p);
            auto& partials=s.readback.data->snapshot;
            CUDA_CHECK(cudaMemcpyAsync(partials.data(),s.snapshotPartials.ptr,sizeof(partials),cudaMemcpyDeviceToHost,stream()));
            CUDA_CHECK(cudaStreamSynchronize(stream()));
            double sum=0,squares=0;
            for(int i=0;i<64;++i) {
                if(partials[i].z!=0)throw std::runtime_error("Non-finite snapshot.");
                frame->maxOmega=std::max(frame->maxOmega,partials[i].x);frame->maxSpeed=std::max(frame->maxSpeed,partials[i].y);
                sum+=partials[64+i].x;squares+=partials[64+i].y;
            }
            double mean=sum/(s.n*s.n),variance=squares/(s.n*s.n)-mean*mean;
            if(!std::isfinite(mean)||!std::isfinite(variance))throw std::runtime_error("Non-finite snapshot moments.");
            frame->mean=float(mean);frame->sigma=float(std::sqrt(std::max(variance,0.)));
            frame->n=s.n;frame->sequence=s.stats.step;frame->time=s.stats.time;frame->hasVelocity=velocity;
            return frame;
        }catch(...){s.failed=true;throw;}
    }
    return {};
}
std::vector<Complex> Solver::spectrum() {
    auto& s=*impl;s.healthy();std::vector<V2> half(size_t(s.n)*s.width);
    CUDA_CHECK(cudaMemcpyAsync(half.data(),s.omega.ptr,s.omega.bytes,cudaMemcpyDeviceToHost,stream()));
    CUDA_CHECK(cudaStreamSynchronize(stream()));
    std::vector<Complex> full(size_t(s.n)*s.n);float scale=1/float(s.n*s.n);
    for(int y=0;y<s.n;++y)for(int x=0;x<s.n;++x) {
        V2 a=x<s.width?half[y*s.width+x]:half[((s.n-y)%s.n)*s.width+s.n-x];
        full[y*s.n+x]={a.x*scale,(x<s.width?a.y:-a.y)*scale};
    }
    return full;
}
std::vector<float> Solver::readFrame(const Frame& f) {
    std::vector<float> values(size_t(3)*f.n*f.n);
    CUDA_CHECK(cudaMemcpy(values.data(),f.data(),values.size()*4,cudaMemcpyDeviceToHost));return values;
}
}
