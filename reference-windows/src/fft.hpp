#pragma once
#include <cuda_runtime.h>
#include <memory>
namespace turbo {
class FFT {
    struct Impl;
    std::unique_ptr<Impl> impl;
public:
    FFT(int n, bool real, int pitch, int cutoff, cudaStream_t stream);
    ~FFT();
    void prepare(int batches);
    void transform(void* buffer, int batches, bool inverse);
    size_t allocatedBytes() const;
};
}
