#pragma once
// Private C++ plugin boundary, built with the bridge. No graphics headers here.
#include "solver.hpp"
#include <filesystem>
namespace turbo {
struct CanvasInfo { int width=0, height=0; bool visible=false; };
struct RenderTiming {
    double uploadSeconds=0, drawSeconds=0, swapSeconds=0;
    uint64_t uploads=0, deviceCopyBytes=0;
};
class LinuxRenderer {
public:
    virtual ~LinuxRenderer() = default;
    virtual CanvasInfo geometry() = 0;
    // Returns a native transport action: 1 toggle, 2 single step, 3 reset.
    virtual int event() = 0;
    virtual RenderTiming present(const std::shared_ptr<Frame>&, const Display&,
                                uint64_t seed, double time, bool running, double scale) = 0;
    virtual void exportPNG(const std::shared_ptr<Frame>&, const Display&,
                           const std::filesystem::path&, int side) = 0;
};
using CreateRenderer = LinuxRenderer* (*)(uintptr_t parent, int device);
}
