#pragma once
#include "solver.hpp"
#include <functional>

namespace turbo::legacy {
// Historical tools/sim contract, deliberately distinct from Config's SplitMix
// initializer, positive curl convention and stage-based application methods.
struct Config {
    int n = 512, steps = 1001, update = 100, seed = 1;
    double reynolds = 10000, k0 = 10, cfl = .25;
    float fixedDt = 0;
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
    Row advanceTo(int target, std::optional<double> until, bool finalCheckpoint,
                  const std::function<void(const Row&)>& checkpoint);
public:
    explicit Solver(const Config& config);
    ~Solver();
    const Initial& initial() const;
    Row initialRow() const;
    Row advance(const std::function<void(const Row&)>& checkpoint = {});
    // Continuous application runs retain the reference's 1/100-step cadence
    // across batches; a batch boundary is not a terminal diagnostic checkpoint.
    Row advanceSteps(int steps, std::optional<double> until = {});
    void setReynolds(double reynolds);
    void setCFL(float cfl);
    cudaStream_t stream() const;
    size_t fftBytes() const;
    void copySpectrumTo(Complex* target, int pitch, float scale) const;
    void loadSpectrum(const Complex* source, int pitch, float scale);
    size_t allocatedBytes() const;
    std::vector<Complex> spectrum() const;
    std::vector<Complex> history() const;
    std::vector<float> velocity() const;
};
}
