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
// Optional standalone frontend. The existing embedded/PNG interface above is
// unchanged; only the desktop host uses these methods on the GL owner thread.
class LinuxDesktopRenderer : public LinuxRenderer {
public:
    virtual void openDesktop(const char* title, int width, int height) = 0;
    virtual void* desktopDisplay() = 0;
    virtual uintptr_t desktopWindow() = 0;
    virtual unsigned renderTexture(const std::shared_ptr<Frame>&, const Display&,
                                   int side, RenderTiming&) = 0;
    virtual void presentDesktop() = 0;
};
}
extern "C" turbo::LinuxDesktopRenderer* ft_gl_create_desktop(int device);
