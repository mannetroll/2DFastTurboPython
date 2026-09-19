#pragma once
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <limits>
#include <optional>
#include <stdexcept>

namespace turbo {
enum class Integrator { imexRK3, ifHeunRK3, etdrk4, cnab2 };
enum class Form { stress, advective };
enum class StartSpectrum { pao, km3 };
inline constexpr const char* methods[] = {"imexRK3", "ifHeunRK3", "etdrk4"
#ifdef FASTTURBO_ENABLE_CNAB2
    , "CNAB2"
#endif
};
inline constexpr const char* forms[] = {"stress", "advective"};
inline constexpr const char* startSpectra[] = {"PAO", "KM3"};
inline double initialReynolds(int n, int k) {
    return std::pow(10., 1.066240 * std::log10(double(n)) - .083911 * std::log10(double(k)) + 1.286396);
}
struct Config {
    int n = 1024, k0 = 4;
    uint64_t seed = 2;
    double reynolds = initialReynolds(1024, 4);
    float cfl = 3, fixedDt = 0;
    Integrator integrator = Integrator::etdrk4;
    Form form = Form::stress;
    StartSpectrum startSpectrum = StartSpectrum::pao;
    int m() const { return 3*n/2; }
    void validate() const {
        if (n < 16 || n > 16384 || n % 2 || k0 <= 0 || !(reynolds > 0) ||
            !std::isfinite(float(1/reynolds)) || !(cfl > 0) || !std::isfinite(cfl) ||
            fixedDt < 0 || !std::isfinite(fixedDt) || int(integrator)<0 || int(integrator)>=int(sizeof(methods)/sizeof(*methods)) || int(form)<0 || int(form)>1 ||
            int(startSpectrum)<0 || int(startSpectrum)>1)
            throw std::invalid_argument("Invalid resolution, Reynolds number, k0, CFL, fixed timestep, method or start spectrum.");
        if(integrator==Integrator::cnab2&&((n&(n-1))||seed<1||seed>5010||form!=Form::stress||
            !std::isfinite(float(reynolds))||!(float(1/float(reynolds))>0)))
            throw std::invalid_argument("CNAB2 requires a power-of-two resolution, seed 1..5010, stress form and a representable positive viscosity.");
    }
    bool operator==(const Config&) const = default;
};
struct Statistics {
    uint64_t step = 0;
    double time = 0, energy = 0, enstrophy = 0, palinstrophy = 0, metric = 0;
    double reynolds = 0, turnover = 0, gpuMilliseconds = 0;
    float dt = 0;
    size_t allocatedBytes = 0;
    size_t fftBytes = 0;
};
struct Display {
    int field = 0, palette = 1, colorRange = 0;
    float exposure = 1.2f, sigma = 2.5f;
    bool flow = false;
    bool velocity() const { return field == 1 || flow; }
    bool operator==(const Display&) const = default;
};
struct RunAverage {
    double simulation = 0, accumulated = 0;
    std::optional<double> since;
    void begin(bool automatic, double now) { if (automatic) { if (!since) since = now; } else stop(now); }
    void complete(double before, double after, bool automatic) { if (automatic && std::isfinite(after) && after >= before) simulation += after-before; }
    void stop(double now) { if (since) accumulated += std::max(0., now-*since); since.reset(); }
    double wall(double now) const { return accumulated + (since ? std::max(0., now-*since) : 0); }
    double rate(double now) const { return wall(now)>0 ? simulation/wall(now) : 0; }
};
struct Regulator {
    double previous = 0, first = 0, filtered = 0;
    std::optional<double> next(double current, double metric, int n, int k0) {
        double raw = 10000*metric;
        if (!(raw>0) || !std::isfinite(raw) || !(current>0) || !std::isfinite(current)) return {};
        if (filtered==0) first=filtered=raw;
        else { first+=(raw-first)/6; filtered+=(first-filtered)/6; }
        double error=(filtered-20)/20;
        if (std::abs(error)<.001) return {};
        double output=std::clamp(.25*error+25*(error-previous),-.01,.01);
        previous=error;
        double ref=initialReynolds(n,k0);
        return std::clamp(current*std::exp(output), std::max(1000.,ref/1000), std::min(1e6,ref*1000));
    }
};
inline bool reached(double time, double target) {
    return time >= target - 32*std::max(std::abs(target), std::numeric_limits<double>::min())*std::numeric_limits<double>::epsilon();
}
}
