#import "MTTFFT2DBackend.h"

#include "MTTVkFFTCore.hpp"

#include <memory>
#include <string>

BOOL MTTVkFFTWasCompiledIn(void) {
#if __has_include("vkFFT.h") && __has_include("Metal/Metal.hpp")
    return YES;
#else
    return NO;
#endif
}

@interface MTTVkFFTBackend () {
    std::unique_ptr<MTTVkFFTCore> _core;
}
@property(nonatomic, readwrite, getter=isAvailable) BOOL available;
@property(nonatomic, readwrite) NSString *backendName;
@property(nonatomic, readwrite, nullable) NSString *lastErrorMessage;
@property(nonatomic, strong) id<MTLDevice> device;
@property(nonatomic, strong) id<MTLCommandQueue> commandQueue;
@property(nonatomic) NSUInteger resolution;
@end

@implementation MTTVkFFTBackend

- (instancetype)initWithDevice:(id<MTLDevice>)device
                   commandQueue:(id<MTLCommandQueue>)commandQueue
                     resolution:(NSUInteger)resolution
                  realToComplex:(BOOL)realToComplex {
    return [self initWithDevice:device commandQueue:commandQueue resolution:resolution
                 realToComplex:realToComplex retainedXCutoff:0];
}

- (instancetype)initWithDevice:(id<MTLDevice>)device
                   commandQueue:(id<MTLCommandQueue>)commandQueue
                     resolution:(NSUInteger)resolution
                  realToComplex:(BOOL)realToComplex
                 retainedXCutoff:(NSUInteger)retainedXCutoff {
    return [self initWithDevice:device commandQueue:commandQueue resolution:resolution
                 realToComplex:realToComplex retainedXCutoff:retainedXCutoff retainedYCutoff:0];
}

- (instancetype)initWithDevice:(id<MTLDevice>)device
                   commandQueue:(id<MTLCommandQueue>)commandQueue
                     resolution:(NSUInteger)resolution
                  realToComplex:(BOOL)realToComplex
                 retainedXCutoff:(NSUInteger)retainedXCutoff
                 retainedYCutoff:(NSUInteger)retainedYCutoff {
    return [self initWithDevice:device commandQueue:commandQueue resolution:resolution
                 realToComplex:realToComplex retainedXCutoff:retainedXCutoff
                 retainedYCutoff:retainedYCutoff complexRowStride:0];
}

- (instancetype)initWithDevice:(id<MTLDevice>)device
                   commandQueue:(id<MTLCommandQueue>)commandQueue
                     resolution:(NSUInteger)resolution
                  realToComplex:(BOOL)realToComplex
                 retainedXCutoff:(NSUInteger)retainedXCutoff
                 retainedYCutoff:(NSUInteger)retainedYCutoff
                complexRowStride:(NSUInteger)complexRowStride {
    self = [super init];
    if (!self) {
        return nil;
    }

    _device = device;
    _commandQueue = commandQueue;
    _resolution = resolution;
    _backendName = realToComplex ? @"VkFFT Metal R2C" : @"VkFFT Metal";
    _core = std::make_unique<MTTVkFFTCore>(
        (__bridge void *)device,
        (__bridge void *)commandQueue,
        static_cast<std::size_t>(resolution),
        realToComplex == YES,
        static_cast<std::size_t>(retainedXCutoff),
        static_cast<std::size_t>(retainedYCutoff),
        static_cast<std::size_t>(complexRowStride)
    );
    _available = _core->available();
    if (!_available) {
        _lastErrorMessage =
            [NSString stringWithUTF8String:_core->lastError().c_str()];
    }
    return self;
}

- (BOOL)encodeBuffer:(id<MTLBuffer>)buffer
          byteOffset:(NSUInteger)byteOffset
          batchCount:(NSUInteger)batchCount
             inverse:(BOOL)inverse
       commandBuffer:(id<MTLCommandBuffer>)commandBuffer {
    if (!_core || !_core->available()) {
        return NO;
    }

    const bool encoded = _core->encode(
        (__bridge void *)buffer,
        static_cast<std::size_t>(byteOffset),
        static_cast<std::size_t>(batchCount),
        inverse == YES,
        (__bridge void *)commandBuffer
    );
    if (!encoded) {
        _lastErrorMessage =
            [NSString stringWithUTF8String:_core->lastError().c_str()];
    }
    return encoded ? YES : NO;
}

@end
