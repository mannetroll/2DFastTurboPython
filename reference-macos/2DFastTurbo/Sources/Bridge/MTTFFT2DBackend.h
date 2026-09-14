#import <Foundation/Foundation.h>
#import <Metal/Metal.h>

NS_ASSUME_NONNULL_BEGIN

/// FFTs are isolated behind this Objective-C boundary so the fluid solver
/// never depends directly on VkFFT's C/metal-cpp API.
@protocol MTTFFT2DBackend <NSObject>

@property(nonatomic, readonly, getter=isAvailable) BOOL available;
@property(nonatomic, readonly) NSString *backendName;
@property(nonatomic, readonly, nullable) NSString *lastErrorMessage;

/// Encodes an in-place, full-complex FP32 2D transform. Consecutive batches
/// use VkFFT's native WHD+batch layout. VkFFT 1.3.4's Metal backend requires
/// byteOffset == 0; batching provides the contiguous multi-field layout.
- (BOOL)encodeBuffer:(id<MTLBuffer>)buffer
          byteOffset:(NSUInteger)byteOffset
          batchCount:(NSUInteger)batchCount
              inverse:(BOOL)inverse
        commandBuffer:(id<MTLCommandBuffer>)commandBuffer
    NS_SWIFT_NAME(encode(buffer:byteOffset:batchCount:inverse:commandBuffer:));

@end

/// VkFFT 1.3.4 Metal backend. When the vendored headers are absent the class
/// remains buildable but reports `available == NO`, allowing the app to show
/// a precise dependency error instead of failing to link.
@interface MTTVkFFTBackend : NSObject <MTTFFT2DBackend>

/// `realToComplex` selects VkFFT's R2C/C2R decomposition: half the memory and,
/// on bandwidth-bound sizes, close to half the time.
- (instancetype)initWithDevice:(id<MTLDevice>)device
                   commandQueue:(id<MTLCommandQueue>)commandQueue
                     resolution:(NSUInteger)resolution
                  realToComplex:(BOOL)realToComplex;

/// Optional frequency-domain cutoff for padded real transforms. A nonzero
/// value keeps |kx| < retainedXCutoff: inverse input outside that band must be
/// zero, and forward output outside it is discarded. Zero is unrestricted.
/// This is fixed for the lifetime of every batch plan owned by this backend.
- (instancetype)initWithDevice:(id<MTLDevice>)device
                   commandQueue:(id<MTLCommandQueue>)commandQueue
                     resolution:(NSUInteger)resolution
                  realToComplex:(BOOL)realToComplex
                 retainedXCutoff:(NSUInteger)retainedXCutoff;

/// Independent cutoffs in both frequency axes. A nonzero Y cutoff similarly
/// keeps |ky| < retainedYCutoff. Inverse omitted inputs must already be zero;
/// forward omitted outputs must not be consumed. Physical samples are retained.
- (instancetype)initWithDevice:(id<MTLDevice>)device
                   commandQueue:(id<MTLCommandQueue>)commandQueue
                     resolution:(NSUInteger)resolution
                  realToComplex:(BOOL)realToComplex
                 retainedXCutoff:(NSUInteger)retainedXCutoff
                 retainedYCutoff:(NSUInteger)retainedYCutoff;

/// Optional main-buffer pitch in complex elements, at least resolution/2+1.
/// Zero uses the packed layout. A custom pitch requires a real transform;
/// its physical row pitch is twice this value. Logical FFT sizes are unchanged.
- (instancetype)initWithDevice:(id<MTLDevice>)device
                   commandQueue:(id<MTLCommandQueue>)commandQueue
                     resolution:(NSUInteger)resolution
                  realToComplex:(BOOL)realToComplex
                 retainedXCutoff:(NSUInteger)retainedXCutoff
                 retainedYCutoff:(NSUInteger)retainedYCutoff
                complexRowStride:(NSUInteger)complexRowStride NS_DESIGNATED_INITIALIZER;

- (instancetype)init NS_UNAVAILABLE;

@end

FOUNDATION_EXPORT BOOL MTTVkFFTWasCompiledIn(void);

NS_ASSUME_NONNULL_END
