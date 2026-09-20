#pragma once
#include <memory>
#include <cstdint>

// X11 events only: the existing Linux renderer owns the window and GL context.
class X11ImGui {
    struct Impl;
    std::unique_ptr<Impl> impl;
public:
    X11ImGui(void* display, uintptr_t window);
    ~X11ImGui();
    bool poll(); // false after WM_DELETE_WINDOW
    void frame(int width, int height);
    void wait(double seconds); // wake immediately for input/window events
    void click(float x, float y, bool control=false); // actual X11 events, acceptance testing only
    void resize(int width, int height, bool exact=false); // exact minimum for oversized acceptance captures
    float dpiScale() const;
    void minimize();
    bool minimized() const;
    void show();
    void maximize(bool enabled);
    void key(unsigned long symbol, unsigned modifiers=0); // X11 acceptance events
    void wheel(float x, float y, bool down);
    void close();
};
