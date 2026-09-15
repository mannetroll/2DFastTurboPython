#pragma once
#include <windows.h>
#include <oleacc.h>
#include <filesystem>
#include <list>
#include <string>

namespace turbo {
enum DesktopCommand {
    SaveSnapshot=1000, CloseWindow, Quit, About, Undo, Redo, Cut, Copy, Paste, SelectAll,
    ToggleRunning, SingleStep, ResetFlow, NewSeed, FocusFlow, ShowSimulation, DismissMessage, Minimize
};
struct RuntimeEnvironment { std::wstring subtitle, details, gpu; };
RuntimeEnvironment runtimeEnvironment(int device);
HMENU createDesktopMenus();
class MenuTheme {
    struct Item {MSAAMENUINFO accessible{};std::wstring text;bool bar=false;};
    std::list<Item> items;
    HBRUSH brush{};
    HFONT font{};
    UINT fontDpi=0;
    HFONT menuFont(HWND owner);
public:
    MenuTheme();
    ~MenuTheme();
    void apply(HMENU menu,bool bar=true);
    void title(HMENU menu,UINT command,const wchar_t* label);
    LRESULT measure(HWND owner,MEASUREITEMSTRUCT& item);
    LRESULT draw(HWND owner,const DRAWITEMSTRUCT& item);
    LRESULT mnemonic(HMENU menu,wchar_t key);
};
// The optional callback runs after the dialog is visible, for native UI validation.
using AboutShown = void (*)(HWND);
bool showAbout(HWND owner, AboutShown shown=nullptr);
}
