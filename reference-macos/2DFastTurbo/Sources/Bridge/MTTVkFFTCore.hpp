#pragma once

#include <cstddef>
#include <memory>
#include <string>

/// Pure C++ boundary around VkFFT/metal-cpp. Keeping these headers out of the
/// Objective-C++ translation unit avoids collisions between Foundation's
/// Objective-C declarations and metal-cpp's Foundation namespace.
class MTTVkFFTCore {
public:
    /// `realToComplex` selects VkFFT's R2C/C2R decomposition. The spectrum is
    /// then a half-plane of (resolution/2 + 1) complex values per row, and the
    /// in-place real view has a row stride of 2*(resolution/2 + 1) floats.
    /// Optional retainedXCutoff keeps |kx| below that cutoff in the frequency
    /// domain; retainedYCutoff does the same for |ky|. Zero leaves that axis
    /// unrestricted. Inverse omitted inputs must be zero. Nonzero cutoffs require
    /// a real transform and must not exceed resolution/2.
    /// Optional complexRowStride sets the real transform's main-buffer row pitch
    /// in complex elements; zero is packed. Physical pitch is twice this value.
    MTTVkFFTCore(
        void *device,
        void *commandQueue,
        std::size_t resolution,
        bool realToComplex,
        std::size_t retainedXCutoff = 0,
        std::size_t retainedYCutoff = 0,
        std::size_t complexRowStride = 0
    );
    ~MTTVkFFTCore();

    MTTVkFFTCore(const MTTVkFFTCore &) = delete;
    MTTVkFFTCore &operator=(const MTTVkFFTCore &) = delete;

    bool available() const;
    const std::string &lastError() const;

    bool encode(
        void *buffer,
        std::size_t byteOffset,
        std::size_t batchCount,
        bool inverse,
        void *commandBuffer
    );

private:
    struct Implementation;
    std::unique_ptr<Implementation> implementation_;
};
