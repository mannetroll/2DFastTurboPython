# Dependencies

The app includes the existing VkFFT 1.3.4 headers and metal-cpp headers, with the
Metal/AppKit ownership patch used by Metal2DTurbo. Licenses are preserved with
the vendored sources. No network fetch or Swift package is needed to build.
`Scripts/bootstrap_vkfft.sh` can restore the pinned dependency if removed.

The Objective-C++ and C++ bridge is derived from Metal2DTurbo and deliberately
keeps metal-cpp Foundation headers out of Objective-C++ translation units.
