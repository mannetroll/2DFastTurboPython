#pragma once
#include "types.hpp"
#include <cuda_runtime.h>
#include <memory>
#include <vector>
namespace turbo {
struct Complex { float x,y; bool operator==(const Complex&) const = default; };
struct Frame {
    std::shared_ptr<void> storage;
    int n = 0;
    uint64_t sequence = 0;
    double time = 0;
    float maxOmega = 0, mean = 0, sigma = 0, maxSpeed = 0;
    bool hasVelocity = false;
    const float* data() const { return static_cast<const float*>(storage.get()); }
};
class Solver {
    struct Impl;
    std::unique_ptr<Impl> impl;
public:
    explicit Solver(Config config, const std::vector<float>& initial = {});
    ~Solver();
    const Config& configuration() const;
    Statistics advance(int steps, std::optional<double> until = {});
    Statistics inspect();
    Statistics statistics() const;
    void reset(const std::vector<float>& initial = {});
    void setReynolds(double re);
    void setCFL(float cfl);
    std::shared_ptr<Frame> snapshot(bool velocity = true);
    std::vector<Complex> spectrum();
    std::vector<float> readFrame(const Frame& frame);
    cudaStream_t stream() const;
};
}
