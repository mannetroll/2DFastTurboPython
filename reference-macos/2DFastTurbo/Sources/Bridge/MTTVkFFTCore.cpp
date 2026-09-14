#include "MTTVkFFTCore.hpp"

#if __has_include("vkFFT.h") && __has_include("Metal/Metal.hpp")
#define MTT_HAS_VKFFT 1
#define VKFFT_BACKEND 5
#include "vkFFT.h"
#if !defined(METAL2DTURBO_VKFFT_APPKIT_OWNERSHIP_PATCHED)
#error "VkFFT 1.3.4 needs the Metal2DTurbo AppKit ownership patch."
#endif
#else
#define MTT_HAS_VKFFT 0
#endif

#include <cstdint>
#include <map>
#include <memory>
#include <sstream>

struct MTTVkFFTCore::Implementation {
#if MTT_HAS_VKFFT
    struct Plan {
        VkFFTApplication application{};
        std::uint64_t bufferSize = 0;
        bool initialized = false;

        ~Plan() {
            if (initialized) {
                deleteVkFFT(&application);
            }
        }
    };

    MTL::Device *device = nullptr;
    MTL::CommandQueue *commandQueue = nullptr;
    std::size_t resolution = 0;
    bool realToComplex = false;
    std::size_t retainedXCutoff = 0;
    std::size_t retainedYCutoff = 0;
    std::size_t complexRowStride = 0;
    /// Plans are keyed by batch count and built on first use. The 3/2-padded
    /// grids reach 6144², where an unused plan would cost both build time and
    /// device memory, so nothing is created speculatively. Resolution, transform
    /// kind and both retained cutoffs are immutable within this backend's cache.
    std::map<std::size_t, std::unique_ptr<Plan>> plans;
#endif
    bool isAvailable = false;
    std::string error;

#if MTT_HAS_VKFFT
    Plan *planFor(std::size_t batchCount) {
        const auto existing = plans.find(batchCount);
        if (existing != plans.end()) {
            return existing->second.get();
        }
        auto created = std::make_unique<Plan>();
        if (!initializePlan(*created, batchCount)) {
            return nullptr;
        }
        Plan *result = created.get();
        plans.emplace(batchCount, std::move(created));
        return result;
    }

    bool initializePlan(Plan &plan, std::size_t batchCount) {
        const std::uint64_t n = static_cast<std::uint64_t>(resolution);
        const std::uint64_t rowComplex = complexRowStride != 0
            ? complexRowStride : (realToComplex ? (n / 2 + 1) : n);
        plan.bufferSize = sizeof(float) * 2 * rowComplex * n * batchCount;

        VkFFTConfiguration configuration{};
        configuration.FFTdim = 2;
        configuration.size[0] = n;
        configuration.size[1] = n;
        configuration.numberBatches =
            static_cast<std::uint64_t>(batchCount);
        // Lookup tables pay off once transforms are bandwidth/compute bound,
        // but add overhead on the launch-bound 192² and 384² padded grids.
        if (realToComplex && resolution >= 768) {
            configuration.useLUT = 1;
        }
        configuration.device = device;
        configuration.queue = commandQueue;
        configuration.bufferSize = &plan.bufferSize;
        if (complexRowStride != 0) {
            configuration.bufferStride[0] = rowComplex;
            configuration.bufferStride[1] = rowComplex * n;
        }
        configuration.normalize = 0;
        if (realToComplex) {
            configuration.performR2C = 1;
        }
        const std::size_t cutoffs[] = {retainedXCutoff, retainedYCutoff};
        for (std::size_t axis = 0; axis < 2; ++axis) {
            if (cutoffs[axis] == 0) continue;
            // Use full-axis frequency indices, including the conjugate side.
            // Guards omit only frequency-domain inverse reads / forward writes;
            // every physical sample and all intermediate FFT work remain valid.
            configuration.frequencyZeroPadding = 1;
            configuration.performZeropadding[axis] = 1;
            configuration.fft_zeropad_left[axis] =
                static_cast<std::uint64_t>(cutoffs[axis]);
            configuration.fft_zeropad_right[axis] =
                n - static_cast<std::uint64_t>(cutoffs[axis]) + 1;
        }

        const VkFFTResult result =
            initializeVkFFT(&plan.application, configuration);
        if (result != VKFFT_SUCCESS) {
            std::ostringstream stream;
            stream << "VkFFT plan creation failed with result "
                   << static_cast<int>(result);
            error = stream.str();
            return false;
        }
        plan.initialized = true;
        return true;
    }
#endif
};

MTTVkFFTCore::MTTVkFFTCore(
    void *device,
    void *commandQueue,
    std::size_t resolution,
    bool realToComplex,
    std::size_t retainedXCutoff,
    std::size_t retainedYCutoff,
    std::size_t complexRowStride
) : implementation_(std::make_unique<Implementation>()) {
#if MTT_HAS_VKFFT
    implementation_->device = reinterpret_cast<MTL::Device *>(device);
    implementation_->commandQueue =
        reinterpret_cast<MTL::CommandQueue *>(commandQueue);
    implementation_->resolution = resolution;
    implementation_->realToComplex = realToComplex;
    implementation_->retainedXCutoff = retainedXCutoff;
    implementation_->retainedYCutoff = retainedYCutoff;
    implementation_->complexRowStride = complexRowStride;
    if (complexRowStride != 0 && (!realToComplex || complexRowStride < resolution / 2 + 1)) {
        implementation_->error = "A custom complex row stride requires a real transform and must hold its half spectrum.";
        return;
    }
    if ((retainedXCutoff != 0 || retainedYCutoff != 0)
        && (!realToComplex || retainedXCutoff > resolution / 2 || retainedYCutoff > resolution / 2)) {
        implementation_->error =
            "Retained frequency cutoffs require a real transform and must not exceed resolution/2.";
        return;
    }
    // Building the scalar plan eagerly both validates the transform size and
    // establishes availability; wider batches are added on demand.
    implementation_->isAvailable =
        implementation_->planFor(1) != nullptr;
#else
    (void)device;
    (void)commandQueue;
    (void)resolution;
    (void)realToComplex;
    (void)retainedXCutoff;
    (void)retainedYCutoff;
    (void)complexRowStride;
    implementation_->error =
        "VkFFT was not present while compiling. Run "
        "Scripts/bootstrap_vkfft.sh, then clean and rebuild.";
#endif
}

MTTVkFFTCore::~MTTVkFFTCore() = default;

bool MTTVkFFTCore::available() const {
    return implementation_->isAvailable;
}

const std::string &MTTVkFFTCore::lastError() const {
    return implementation_->error;
}

bool MTTVkFFTCore::encode(
    void *buffer,
    std::size_t byteOffset,
    std::size_t batchCount,
    bool inverse,
    void *commandBuffer
) {
#if MTT_HAS_VKFFT
    if (!implementation_->isAvailable) {
        return false;
    }
    // VkFFT 1.3.4 does not emit the Metal declaration of PushConsts when
    // specifyOffsetsAtLaunch is enabled. All solver buffers begin at offset
    // zero (the four fields are represented by numberBatches), so dynamic
    // launch offsets are neither needed nor safe on this backend version.
    if (byteOffset != 0) {
        implementation_->error =
            "VkFFT 1.3.4 Metal plans require a zero byte offset.";
        return false;
    }

    Implementation::Plan *plan = implementation_->planFor(batchCount);
    if (!plan) {
        return false;
    }

    auto *metalBuffer = reinterpret_cast<MTL::Buffer *>(buffer);
    auto *metalCommandBuffer =
        reinterpret_cast<MTL::CommandBuffer *>(commandBuffer);
    MTL::ComputeCommandEncoder *encoder =
        metalCommandBuffer->computeCommandEncoder();
    if (!encoder) {
        implementation_->error =
            "VkFFT could not create a Metal compute encoder.";
        return false;
    }

    VkFFTLaunchParams launch{};
    launch.buffer = &metalBuffer;
    launch.commandBuffer = metalCommandBuffer;
    launch.commandEncoder = encoder;

    const VkFFTResult result =
        VkFFTAppend(&plan->application, inverse ? 1 : -1, &launch);
    encoder->endEncoding();

    if (result != VKFFT_SUCCESS) {
        std::ostringstream stream;
        stream << "VkFFT append failed with result "
               << static_cast<int>(result);
        implementation_->error = stream.str();
        return false;
    }
    return true;
#else
    (void)buffer;
    (void)byteOffset;
    (void)batchCount;
    (void)inverse;
    (void)commandBuffer;
    return false;
#endif
}
