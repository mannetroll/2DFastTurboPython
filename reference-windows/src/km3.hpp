#pragma once
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <vector>

namespace turbo {
// cudaturbo's PAO-k^-3 shell redistribution. Work stays on the host during
// initialization only; callers retain their spectrum layout and curl convention.
// energy(x,z) is |u|^2+|v|^2 in Fourier-series normalization. store receives
// float32 velocity coefficients (u.real, u.imag, v.real, v.imag).
template<class Energy, class Store>
void reshapeKM3(int n, double k0, uint64_t seed, Energy energy, Store store) {
    const int half=n/2;
    auto hash=[seed](int x,int z,int salt) {
        const double a=(double(x)+1.)*12.9898+(double(z)+1.)*78.233+
            (double(seed)+1.)*.010001+(double(salt)+1.)*37.719;
        const double s=std::sin(a)*43758.5453123;
        return s-std::floor(s);
    };
    std::vector<double> shells(half+1),weights(half+1),target(half+1);
    for(int x=0;x<half;++x)for(int z=0;z<n;++z) {
        const double y=z<half?z:z-n,k=std::sqrt(double(x)*x+y*y);
        const int shell=int(k+.5),zh=x==0&&z>half?n-z:z;
        if(k>0&&k<=half&&shell<=half) {
            const double multiplicity=x==0?1.:2.;
            shells[shell]+=multiplicity*energy(x,z);
            weights[shell]+=multiplicity*(.75+.50*hash(x,zh,17));
        }
    }
    k0=std::max(1.,k0);
    double original=0,total=0;
    for(int shell=1;shell<=half;++shell) {
        const double k=shell,low=k/k0;
        target[shell]=k<k0?(low*low*low*low)/(k0*k0*k0):1./(k*k*k);
        original+=shells[shell];total+=target[shell];
    }
    if(!(original>0&&total>0))return;
    const double norm=original/total;
    for(int x=0;x<half;++x)for(int z=0;z<n;++z) {
        const double y=z<half?z:z-n,k=std::sqrt(double(x)*x+y*y);
        const int shell=int(k+.5),zh=x==0&&z>half?n-z:z;
        float ur=0,ui=0,vr=0,vi=0;
        if(k>0&&k<=half&&shell<=half&&weights[shell]>0) {
            const double power=norm*target[shell]*(.75+.50*hash(x,zh,17))/weights[shell];
            const double sign=x==0&&z>half?-1.:1.;
            const double phase=sign*2.*3.14159265358979323846*hash(x,zh,53);
            const float cr=float(std::cos(phase)),ci=float(std::sin(phase));
            if(x==0) {const float amplitude=float(std::sqrt(power));ur=amplitude*cr;ui=amplitude*ci;}
            else {
                const float amplitude=float(std::sqrt(power/(1.+y*y/(double(x)*x))));
                vr=amplitude*cr;vi=amplitude*ci;
                const float factor=float(-y/x);ur=factor*vr;ui=factor*vi;
            }
        }
        store(x,z,ur,ui,vr,vi);
    }
}
}
