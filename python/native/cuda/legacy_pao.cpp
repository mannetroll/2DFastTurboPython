#include "legacy_cnab2.hpp"
#include "km3.hpp"
#include <array>
#include <numbers>

namespace turbo::legacy {
void Config::validate() const {
    // Preserve the historical power-of-two domain, without the old 2048 cap.
    if(n<16 || n>16384 || (n&(n-1)) || steps<0 || update<1 || seed<1 || seed>5010 ||
       !(reynolds>0) || !(k0>0) || !(cfl>0) || int(startSpectrum)<0 || int(startSpectrum)>1 ||
       !std::isfinite(float(reynolds)) || !std::isfinite(float(k0)) || !std::isfinite(float(cfl)) ||
       !(float(1/float(reynolds))>0))
        throw std::invalid_argument("Legacy mode requires power-of-two N=16..16384, positive Re/K0/CFL, steps>=0, update>0, seed=1..5010.");
}
Initial initialize(const Config& c) {
    c.validate();
    // Native translation of _pao_build_ur_and_stats_impl in tools/turbo_simulator.py.
    // This is the historical LCG, not the application's SplitMix PAO spectrum.
    const int n=c.n, half=n/2;
    const size_t count=size_t(n)*half;
    const float pi=std::numbers::pi_v<float>, k0=float(c.k0), norm=pi*k0*k0;
    Initial out; out.velocity.resize(2*count); out.omega.resize(count);
    int seed=c.seed;
    auto next=[&] { seed=int((int64_t(seed)*420029+2017)%5011); return float(seed)/5011.f; };
    std::array<float,97> random{};
    for(int i=0;i<97;++i)next();
    for(auto& v:random)v=next();
    for(int z=0;z<n;++z)for(int x=0;x<half;++x) {
        const float y=float(z<half?z:z-n), ax=float(x), r=next();
        const int index=std::clamp(int(double(r)*97),0,96);
        const float phase=2.f*pi*random[index]; random[index]=r;
        const float k=std::sqrt(ax*ax+y*y), ratio=k/k0;
        const float power=std::exp(-ratio*ratio);
        const float amplitude=std::sqrt(power/(x==0?norm:(1.f+(y*y)/(ax*ax))*norm));
        const Complex direction{amplitude*std::cos(phase),amplitude*std::sin(phase)};
        const size_t i=size_t(z)*half+x;
        if(x==0)out.velocity[i]=direction;
        else {
            out.velocity[count+i]=direction;
            const float factor=-(y/ax);
            out.velocity[i]={factor*direction.x,factor*direction.y};
        }
    }
    out.velocity[0]={}; out.velocity[count]={};
    for(int component=0;component<2;++component) {
        for(int z=1;z<half;++z) {
            auto v=out.velocity[component*count+size_t(z)*half];
            out.velocity[component*count+size_t(n-z)*half]={v.x,-v.y};
        }
        for(int x=0;x<half;++x)out.velocity[component*count+size_t(half)*half+x]={};
    }
    if(c.startSpectrum==StartSpectrum::km3) {
        reshapeKM3(n,double(k0),uint64_t(c.seed),[&](int x,int z) {
            const size_t i=size_t(z)*half+x;
            const auto u=out.velocity[i],v=out.velocity[count+i];
            return double(u.x)*u.x+double(u.y)*u.y+double(v.x)*v.x+double(v.y)*v.y;
        },[&](int x,int z,float ur,float ui,float vr,float vi) {
            const size_t i=size_t(z)*half+x;
            out.velocity[i]={ur,ui};out.velocity[count+i]={vr,vi};
        });
    }
    double a1=0,a2=0,a3=0,a4=0,a5=0,a6=0;
    // Preserve the initializer's x-major FP64 diagnostic accumulation order.
    for(int x=0;x<half;++x)for(int z=0;z<n;++z) {
        size_t i=size_t(z)*half+x;
        auto u=out.velocity[i],v=out.velocity[count+i];
        double uu=double(u.x)*u.x+double(u.y)*u.y, vv=double(v.x)*v.x+double(v.y)*v.y;
        double y=z<half?z:z-n, weight=x==0?1:2, xx=double(x)*x, yy=y*y;
        a1+=weight*uu; a2+=weight*vv;
        a3+=weight*uu*xx; a4+=weight*uu*yy;
        a5+=weight*vv*xx; a6+=weight*vv*yy;
        // omega = dy(u) - dx(v), as in dns_calcom_from_uc_full.
        float fy=float(y), fx=float(x);
        out.omega[i]={-fy*u.y+fx*v.y,fy*u.x-fx*v.x};
    }
    out.energy=a1+a2; out.wiwi=a3+a4+a5+a6;
    out.viscosity=float(1./float(c.reynolds)); out.seedOut=seed;
    return out;
}
}
