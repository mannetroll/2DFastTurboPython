from pathlib import Path

here = Path(__file__).resolve().parent
reference = here.parents[1]
source = here / "source"
source.mkdir(exist_ok=True)
app = (reference / "src/app.cpp").read_text(encoding="utf-8")

def replace(old, new):
    global app
    assert app.count(old) == 1, old
    app = app.replace(old, new)

# Presentation-only changes in an isolated copy of the existing GUI test.
replace('if(done||!controller||checking)return;',
        'if(done||!controller||checking)return;\n    static double nextVideoCheck=0;if(nowSeconds()<nextVideoCheck)return;')
replace('auto& check=checks.front();if(check.started==0)check.started=nowSeconds();',
        'auto& check=checks.front();if(check.started==0){check.started=nowSeconds();std::cerr<<"CHECK "<<check.name<<std::endl;}')
replace('checks.pop_front();}', 'checks.pop_front();nextVideoCheck=nowSeconds()+.14;}')
replace('view.rate>0&&presentations>0;', 'view.rate>0&&presentations>0&&nowSeconds()-firstPresentation>2.5;')
replace('    bool checking=false;', '    HMENU videoMenu=nullptr;\n    bool checking=false;')
replace('        case WM_ERASEBKGND:return 1;', '''        case WM_APP+8:{
            HiliteMenuItem(hwnd,videoMenu,UINT(w),MF_BYCOMMAND|MF_HILITE);
            RECT item{};if(GetMenuItemRect(hwnd,videoMenu,UINT(w)-1,&item))std::cerr<<"POINTER "<<(item.left+item.right)/2<<" "<<(item.top+item.bottom)/2<<std::endl;
            return 0;}
        case WM_APP+9:EndMenu();return 0;
        case WM_ERASEBKGND:return 1;''')
start = app.index('        std::thread menuInput;')
end = app.index('    void activate(int id)', start)
app = app[:start] + '''        std::thread menuInput;
        int selected=std::exchange(automatedMenuSelection,-1);videoMenu=menu;
        if(selected>=0){HWND owner=hwnd;menuInput=std::thread([owner,selected]{
            Sleep(180);PostMessageW(owner,WM_APP+8,WPARAM(selected+1),0);
            Sleep(270);PostMessageW(owner,WM_APP+9,0,0);});}
        int result=TrackPopupMenu(menu,TPM_RETURNCMD|TPM_NONOTIFY|TPM_LEFTALIGN,p.x,p.y,0,hwnd,nullptr);
        if(menuInput.joinable())menuInput.join();DestroyMenu(menu);videoMenu=nullptr;
        return selected>=0?selected:result?result-1:-1;
    }
''' + app[end:]
replace('        switch(id) {\n        case Focus:',
        '        if(options.liveCheck&&found!=hits.end()){POINT p{LONG((r.left+r.right)*.5f*scale),LONG((r.top+r.bottom)*.5f*scale)};ClientToScreen(hwnd,&p);std::cerr<<"POINTER "<<p.x<<" "<<p.y<<std::endl;}\n        switch(id) {\n        case Focus:')
replace('        float f=std::clamp((x-h->rect.left)',
        '        if(options.liveCheck){POINT p{LONG(x*scale),LONG((h->rect.top+h->rect.bottom)*.5f*scale)};ClientToScreen(hwnd,&p);std::cerr<<"POINTER "<<p.x<<" "<<p.y<<std::endl;}\n        float f=std::clamp((x-h->rect.left)')
(source / "app.cpp").write_text(app, encoding="utf-8")
desktop = (reference / "src/desktop_ui.cpp").read_text(encoding="utf-8")
assert desktop.count('SetTimer(window,1,200,nullptr)') == 1
desktop = desktop.replace('SetTimer(window,1,200,nullptr)', 'SetTimer(window,1,1200,nullptr)')
(source / "desktop_ui.cpp").write_text(desktop, encoding="utf-8")
r = reference.as_posix()
(source / "CMakeLists.txt").write_text(f'''cmake_minimum_required(VERSION 3.26)
project(FastTurboVideo LANGUAGES CXX RC)
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_MSVC_RUNTIME_LIBRARY MultiThreaded)
find_package(CUDAToolkit 13.0 REQUIRED)
add_executable(2DFastTurbo-video WIN32
  "{r}/src/main.cpp" app.cpp desktop_ui.cpp "{r}/src/renderer.cpp"
  "{r}/src/controller.cpp" "{r}/src/application.rc" "{r}/build-dist/embedded/embedded.rc")
target_include_directories(2DFastTurbo-video PRIVATE "{r}/src")
target_compile_definitions(2DFastTurbo-video PRIVATE NOMINMAX WIN32_LEAN_AND_MEAN UNICODE _UNICODE)
target_compile_options(2DFastTurbo-video PRIVATE /utf-8 /W4 /fp:strict)
target_link_libraries(2DFastTurbo-video PRIVATE "{r}/build-dist/turbo_core.lib"
  CUDA::cudart_static CUDA::cuda_driver CUDA::nvrtc_static CUDA::nvrtc_builtins_static
  CUDA::nvptxcompiler_static user32 ws2_32 d3d11 dxgi d2d1 dwrite windowscodecs dwmapi comdlg32 shell32 ole32 winmm)
''', encoding="utf-8")
print(source)
