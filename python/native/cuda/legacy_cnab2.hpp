#pragma once
#include "solver.hpp"
#include <functional>

namespace turbo::legacy {
// Historical tools/sim contract, deliberately distinct from Config's SplitMix
// initializer, positive curl convention and stage-based application methods.
struct Config {
    int n = 512, steps = 1001, update = 100, seed = 1;
    double reynolds = 10000, k0 = 10, cfl = .25;
    StartSpectrum startSpectrum = StartSpectrum::pao;
    int m() const { return 3*n/2; }
    void validate() const;
};
struct Initial {
    std::vector<Complex> velocity, omega;
    double energy = 0, wiwi = 0;
    float viscosity = 0;
    int seedOut = 0;
};
Initial initialize(const Config& config);
struct Row {
    int iteration = 0;
    double time = 0, stateTime = 0, dt = 0, cn = 1, cflMaximum = 0, tau = 0;
};
class Solver {
    struct Impl;
    std::unique_ptr<Impl> impl;
public:
    explicit Solver(const Config& config);
    ~Solver();
    const Initial& initial() const;
    Row initialRow() const;
    Row advance(const std::function<void(const Row&)>& checkpoint = {});
    size_t allocatedBytes() const;
    std::vector<Complex> spectrum() const;
    std::vector<Complex> history() const;
    std::vector<float> velocity() const;
};
}
