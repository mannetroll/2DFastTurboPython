#define GL_GLEXT_PROTOTYPES
#include <GL/gl.h>
#include <GL/glext.h>
#include "controller.hpp"
#include "cuda_loader.hpp"
#include "cuda_support.cuh"
#include "render_api.hpp"
#include "x11_imgui.hpp"
#include "imgui.h"
#include "imgui_impl_opengl3.h"
#include "presentation.hpp"
#include "json.hpp"
#include <png.h>
#include <charconv>
#include <chrono>
#include <fstream>
#include <iostream>
#include <map>
#include <set>
#include <filesystem>
#include "licenses.hpp"
#include <functional>
#include <cstdio>
#include <X11/keysym.h>
namespace ImStb {
#include "imstb_textedit.h"
}

namespace {
using Json=nlohmann::json;
struct Arguments {
    turbo::Options solver;
    int device=0;
    int width=952,height=900;
    float scale=0;
    bool focus=false;
    double interval=.1,benchmarkSeconds=0;
    bool visualization=true,selfTest=false,help=false,licenses=false;
    std::string output,capture;
    std::string testExit="wm";
    Arguments(){solver.config.n=2048;solver.config.k0=10;
        solver.config.reynolds=turbo::initialReynolds(2048,10);solver.regulated=false;}
};
template<class T>T number(const std::string& value,const std::string& name){
    T result{};auto [end,error]=std::from_chars(value.data(),value.data()+value.size(),result);
    if(error!=std::errc{}||end!=value.data()+value.size()||!std::isfinite(double(result)))
        throw std::invalid_argument("Invalid value for "+name);
    return result;
}
Arguments parse(int argc,char** argv){
    Arguments a;bool explicitRe=false;
    for(int i=1;i<argc;++i){
        std::string key=argv[i];
        auto value=[&]()->std::string{if(i+1==argc)throw std::invalid_argument("Missing value for "+key);return argv[++i];};
        if(key=="--help"||key=="-h")a.help=true;
        else if(key=="--licenses")a.licenses=true;
        else if(key=="--resolution")a.solver.config.n=number<int>(value(),key);
        else if(key=="--reynolds"){a.solver.config.reynolds=number<double>(value(),key);explicitRe=true;}
        else if(key=="--k0")a.solver.config.k0=number<int>(value(),key);
        else if(key=="--cfl")a.solver.config.cfl=number<float>(value(),key);
        else if(key=="--seed")a.solver.config.seed=number<uint64_t>(value(),key);
        else if(key=="--device")a.device=number<int>(value(),key);
        else if(key=="--width")a.width=number<int>(value(),key);
        else if(key=="--height")a.height=number<int>(value(),key);
        else if(key=="--ui-scale")a.scale=number<float>(value(),key);
        else if(key=="--focus")a.focus=true;
        else if(key=="--interval-ms")a.interval=number<double>(value(),key)/1000;
        else if(key=="--benchmark-seconds"){
            a.benchmarkSeconds=number<double>(value(),key);
            if(a.benchmarkSeconds<=0)throw std::invalid_argument("Benchmark seconds must be positive.");
        }
        else if(key=="--no-visualization")a.visualization=false;
        else if(key=="--paused")a.solver.paused=true;
        else if(key=="--self-test")a.selfTest=true;
        else if(key=="--test-exit")a.testExit=value();
        else if(key=="--output")a.output=value();
        else if(key=="--capture")a.capture=value();
        else throw std::invalid_argument("Unknown option: "+key);
    }
    if(a.help||a.licenses)return a;
    if(a.testExit!="wm"&&(!a.selfTest||(a.testExit!="close-menu"&&a.testExit!="quit-menu"&&a.testExit!="ctrl-w"&&a.testExit!="ctrl-q")))
        throw std::invalid_argument("--test-exit requires --self-test and close-menu, quit-menu, ctrl-w or ctrl-q.");
    if(a.width<760||a.width>7680||a.height<560||a.height>4320||a.scale<0||a.scale>3||(a.scale>0&&a.scale<1))
        throw std::invalid_argument("Window size must be 760..7680 by 560..4320, UI scale 1..3 (0: desktop DPI).");
    if(a.device<0||a.interval<.004||a.interval>10||a.benchmarkSeconds<0)
        throw std::invalid_argument("Device must be nonnegative, interval 4..10000 ms, and benchmark duration nonnegative.");
    if(!explicitRe)a.solver.config.reynolds=turbo::initialReynolds(a.solver.config.n,a.solver.config.k0);
    if(a.benchmarkSeconds>0){
        // The existing controller owns timed execution, warmup/reset and batches.
        a.solver.guiBenchmark=true;a.solver.wallSeconds=a.benchmarkSeconds;
        a.solver.fixedBatch=true;a.solver.batch=8;a.solver.paused=false;
        if(!explicitRe)a.solver.config.reynolds=27898.36614974335; // 2dbench workload
    }
    if(a.selfTest){
        if(a.benchmarkSeconds||!a.visualization)throw std::invalid_argument("Self-test requires visualization and no benchmark.");
        a.solver.config.n=128;a.solver.config.k0=4;a.solver.paused=true;
    }
    if(!std::isfinite(a.solver.config.reynolds))throw std::invalid_argument("Reynolds must be finite.");
    a.solver.config.validate();return a;
}
void write(const Arguments& args,const Json& result){
    if(!args.output.empty()){
        std::ofstream out(args.output);if(!(out<<result.dump(2)<<'\n'))throw std::runtime_error("Cannot write "+args.output);
    }
    std::cout<<result.dump(2)<<'\n';
}
void capture(const std::string& path,int w,int h){
    if(path.empty())return;
    std::vector<unsigned char> bottom(size_t(w)*h*4),top(bottom.size());
    glReadBuffer(GL_BACK);glReadPixels(0,0,w,h,GL_RGBA,GL_UNSIGNED_BYTE,bottom.data());
    if(glGetError()!=GL_NO_ERROR)throw std::runtime_error("Cannot capture desktop framebuffer.");
    for(int y=0;y<h;++y)std::copy_n(bottom.data()+size_t(h-1-y)*w*4,size_t(w)*4,top.data()+size_t(y)*w*4);
    png_image png{};png.version=PNG_IMAGE_VERSION;png.width=unsigned(w);png.height=unsigned(h);png.format=PNG_FORMAT_RGBA;
    if(!png_image_write_to_file(&png,path.c_str(),0,top.data(),0,nullptr))throw std::runtime_error(png.message);
}
struct ImGuiOwner {
    bool renderer=false;
    ImGuiOwner(){IMGUI_CHECKVERSION();ImGui::CreateContext();}
    ~ImGuiOwner(){if(renderer)ImGui_ImplOpenGL3_Shutdown();ImGui::DestroyContext();}
};

class Application {
    const Arguments& args;
    turbo::LinuxDesktopRenderer& renderer;
    X11ImGui& platform;
    std::unique_ptr<turbo::Controller> controller;
    turbo::Config edit;
    turbo::Display display,lastDisplay;
    std::shared_ptr<turbo::Frame> shown;
    bool visualize,workerVisible=true;
    float intervalMs;
    unsigned texture=0;
    int textureSide=0;
    double nextUpdate=0;
    uint64_t seenGeneration=0,presentations=0,fieldUpdates=0;
    turbo::RenderTiming timing;
    std::string gpu,error,glRenderer;
    std::map<std::string,ImVec2> controls;
    double warmupStart=0,measurementStart=0,started=turbo::nowSeconds();
    int stage=0;
    double stageTime=started;
    uint64_t pausedStep=0,previousHash=0;
    std::set<uint64_t> fieldHashes;
    Json checks=Json::array();
    bool completed=false;
    bool captured=false;
    float scale=1;
    bool focused=false,about=false,saveRequested=false;
    char seedText[32]{},snapshotPath[1024]="FastTurbo.png";
    std::shared_ptr<turbo::Frame> exportFrame;
    turbo::Display exportDisplay;
    std::string environment;
    ImGuiID editorId=0;
    ImGuiWindow* editorWindow=nullptr;
    bool editorTemporary=false;
    ImGuiKey editKey=ImGuiKey_None;
    bool releaseEditKey=false,restoreEditor=false;
    int editDelay=0;
    bool clipboardAvailable=false,menuWasOpen=false;
    ImVec2 imagePos{},imageSize{},inspectorPos{},inspectorSize{},viewportPos{},viewportSize{};
    float inspectorScroll=0,inspectorMaxScroll=0,testScroll=-1;
    std::string testCapture;
    std::string testFailure;
    void remember(const char* name){if(args.selfTest){auto lo=ImGui::GetItemRectMin(),hi=ImGui::GetItemRectMax();controls[name]={(lo.x+hi.x)/2,(lo.y+hi.y)/2};}}
    void click(const char* name){auto p=controls.at(name);platform.click(p.x,p.y);}
    void check(bool condition,const char* message){if(!condition)throw std::runtime_error(std::string("Self-test: ")+message+" [seed editor: "+seedText+"]");}
    void next(const char* message){checks.push_back(message);std::cerr<<"PASS "<<stage<<": "<<message<<'\n';++stage;stageTime=turbo::nowSeconds();}
    uint64_t pixels(){
        // Explicit acceptance readback only. Production display never reads pixels.
        std::vector<uint32_t> data(size_t(textureSide)*textureSide);
        glBindTexture(GL_TEXTURE_2D,texture);glGetTexImage(GL_TEXTURE_2D,0,GL_RGBA,GL_UNSIGNED_BYTE,data.data());
        check(glGetError()==GL_NO_ERROR,"texture readback");
        std::set<uint32_t> colors;uint64_t hash=14695981039346656037ull;
        for(auto pixel:data){colors.insert(pixel);hash=(hash^pixel)*1099511628211ull;}
        check(colors.size()>100,"field must contain more than 100 colors");
        return hash;
    }
#include "tests/desktop_checks.inc"
    void configure(){
        try{edit.validate();controller->configure(edit);error.clear();}
        catch(const std::exception& e){error=e.what();}
        edit=controller->view().config;
        std::snprintf(seedText,sizeof(seedText),"%llu",static_cast<unsigned long long>(edit.seed));
    }
    enum class Action {toggle,step,reset,seed,focus,save,close,about,show,dismiss,minimize};
    void action(Action a,const turbo::ViewState& s){
        switch(a){
        case Action::toggle:if(s.ready)controller->running(!s.running);break;
        case Action::step:if(s.ready)controller->singleStep();break;
        case Action::reset:if(s.ready)controller->reset();break;
        case Action::seed:edit=s.config;edit.seed=edit.seed==UINT64_MAX?0:edit.seed+1;
            if(edit.integrator==turbo::Integrator::cnab2&&(edit.seed<1||edit.seed>5010))edit.seed=1;
            configure();break;
        case Action::focus:focused=!focused;break;
        case Action::save:if(shown&&!exportFrame){exportFrame=shown;exportDisplay=lastDisplay;saveRequested=true;}break;
        case Action::close:platform.close();break;
        case Action::about:about=true;break;
        case Action::show:platform.show();visualize=true;nextUpdate=0;break;
        case Action::dismiss:error.clear();controller->dismissError();break;
        case Action::minimize:platform.minimize();break;
        }
    }
    void shortcuts(const turbo::ViewState& s){
        auto& io=ImGui::GetIO();
        if(ImGui::IsPopupOpen("",ImGuiPopupFlags_AnyPopupId|ImGuiPopupFlags_AnyPopupLevel))return;
        auto pressed=[](ImGuiKey k){return ImGui::IsKeyPressed(k,false);};
        if(io.KeyCtrl&&!io.KeyAlt){
            if(pressed(ImGuiKey_Q)||pressed(ImGuiKey_W))action(Action::close,s);
            if(pressed(ImGuiKey_S))action(Action::save,s);
            if(pressed(ImGuiKey_M))action(Action::minimize,s);
            if(!io.WantTextInput&&io.KeyShift&&pressed(ImGuiKey_R)&&!args.benchmarkSeconds)action(Action::seed,s);
        }
        if(io.WantTextInput||ImGui::IsAnyItemActive()||io.KeyCtrl||io.KeyAlt||io.KeySuper)return;
        if(pressed(ImGuiKey_F))action(Action::focus,s);
        // Arrow/Space belong to keyboard navigation while a widget has focus.
        if(!args.benchmarkSeconds){
            if(pressed(ImGuiKey_Space)&&!GImGui->NavCursorVisible)action(Action::toggle,s);
            if(pressed(ImGuiKey_RightArrow)&&!GImGui->NavCursorVisible)action(Action::step,s);
            if(pressed(ImGuiKey_R))action(Action::reset,s);
        }
    }
    bool menuItem(const char* label,const char* shortcut,Action a,const turbo::ViewState& s,bool enabled=true){
        bool hit=ImGui::MenuItem(label,shortcut,false,enabled);
        if(args.selfTest)remember((std::string("Menu ")+label).c_str());
        if(hit)action(a,s);
        return hit;
    }
    void menus(const turbo::ViewState& s){
        auto& g=*GImGui;
        if(g.ActiveId&&g.InputTextState.ID==g.ActiveId){editorId=g.ActiveId;editorWindow=g.ActiveIdWindow;editorTemporary=g.TempInputId==editorId;}
        else if(!menuWasOpen&&g.OpenPopupStack.empty()&&!ImGui::GetIO().MouseClicked[0])editorId=0;
        if(!ImGui::BeginMenuBar())return;
        bool menuOpen=false;
        auto begin=[&](const char* name){bool open=ImGui::BeginMenu(name);menuOpen|=open;return open;};
        bool available=s.ready&&!args.benchmarkSeconds;
        if(begin("File")){
            menuItem("Save Snapshot…","Ctrl+S",Action::save,s,shown&&!exportFrame);
            menuItem("Close Window","Ctrl+W",Action::close,s);ImGui::Separator();
            menuItem("About 2DFastTurbo",nullptr,Action::about,s);ImGui::Separator();
            menuItem("Quit 2DFastTurbo","Ctrl+Q",Action::close,s);ImGui::EndMenu();
        }remember("File");
        if(begin("Edit")){
            if(ImGui::IsWindowAppearing()){const char* text=ImGui::GetClipboardText();clipboardAvailable=text&&*text;}
            auto* state=editorId?ImGui::GetInputTextState(editorId):nullptr;
            const char* names[]={"Undo","Redo","Cut","Copy","Paste","Select All"};
            const char* labels[]={"Ctrl+Z","Ctrl+Y","Ctrl+X","Ctrl+C","Ctrl+V","Ctrl+A"};
            const ImGuiKey keys[]={ImGuiKey_Z,ImGuiKey_Y,ImGuiKey_X,ImGuiKey_C,ImGuiKey_V,ImGuiKey_A};
            bool enabled[]={state&&state->Stb->undostate.undo_point>0,state&&state->Stb->undostate.redo_point<99,
                state&&state->HasSelection(),state&&state->HasSelection(),state&&clipboardAvailable,state&&state->TextLen>0};
            for(int i=0;i<6;++i){
                if(i==2)ImGui::Separator();
                if(ImGui::MenuItem(names[i],labels[i],false,enabled[i])){
                    // Defer focus until the menu's mouse click has finished;
                    // InputText otherwise treats that click as an outside click.
                    editKey=keys[i];restoreEditor=true;
                }remember(names[i]);
            }
            ImGui::EndMenu();
        }remember("Edit");
        if(begin("Simulation")){
            menuItem(s.running?"Pause":"Resume","Space",Action::toggle,s,available);
            menuItem("Single Step","Right",Action::step,s,available);ImGui::Separator();
            menuItem("Reset Flow","R",Action::reset,s,available);
            menuItem("New Initial Condition","Ctrl+Shift+R",Action::seed,s,available);ImGui::EndMenu();
        }remember("Simulation");
        if(begin("View")){
            menuItem(focused?"Show Controls":"Focus on Flow","F",Action::focus,s);
            menuItem("Show Simulation",nullptr,Action::show,s);
            menuItem("Dismiss Message",nullptr,Action::dismiss,s,!error.empty()||!s.error.empty());ImGui::EndMenu();
        }remember("View");
        if(begin("Window")){
            menuItem("Minimize","Ctrl+M",Action::minimize,s);ImGui::EndMenu();
        }remember("Window");
        ImGui::EndMenuBar();
        if(menuWasOpen&&!menuOpen&&editorId&&!restoreEditor&&ImGui::IsKeyPressed(ImGuiKey_Escape,false)){
            editKey=ImGuiKey_None;restoreEditor=true;
        }
        menuWasOpen=menuOpen;
    }
    void dialogs(){
        if(about){ImGui::OpenPopup("About 2DFastTurbo");about=false;}
        ImGui::SetNextWindowSize({490*scale,0},ImGuiCond_Appearing);
        if(ImGui::BeginPopupModal("About 2DFastTurbo",nullptr,ImGuiWindowFlags_AlwaysAutoResize)){
            auto p=ImGui::GetCursorScreenPos();desktop::mark({p.x+32*scale,p.y+32*scale},32*scale);
            ImGui::Dummy({64*scale,64*scale});ImGui::SameLine(0,18*scale);ImGui::BeginGroup();
            ImGui::PushFont(presentation::titleFont,16);ImGui::TextUnformatted("2DFastTurbo");ImGui::PopFont();
            ImGui::TextUnformatted("Version 0.1.2");ImGui::Spacing();
            ImGui::PushFont(nullptr,13);ImGui::PushTextWrapPos(ImGui::GetCursorPosX()+360*scale);
            ImGui::TextWrapped("GPU simulation of decaying, homogeneous 2D turbulence. Fourier pseudospectral methods with 3/2-rule dealiasing and VkFFT transforms. ETDRK4 time stepping by default.");
            ImGui::PopTextWrapPos();ImGui::Spacing();ImGui::Spacing();
            auto centered=[&](const char* text){
                float inset=(360*scale-ImGui::CalcTextSize(text).x)*.5f;
                ImGui::Indent(inset);ImGui::TextUnformatted(text);ImGui::Unindent(inset);
            };
            centered("Navier–Stokes equation");ImGui::Spacing();
            ImGui::PushFont(presentation::titleFont,17);
            centered("ρ(∂v/∂t + v·∇v) = −∇p + μ∇²v");
            ImGui::PopFont();ImGui::PopFont();ImGui::EndGroup();ImGui::Spacing();ImGui::Spacing();
            ImGui::SeparatorText("System Information");
            ImGui::TextWrapped("%s",environment.c_str());
            ImGui::TextWrapped("OpenGL: %s",glRenderer.c_str());
            ImGui::TextUnformatted("Native C++ / Dear ImGui / CUDA / VkFFT\nX11 / GLX (XWayland on Wayland)");
            ImGui::Spacing();ImGui::AlignTextToFramePadding();
            ImGui::TextUnformatted("Copyright © 2026 Torbjörn Sjögren");ImGui::SameLine();
            ImGui::SetCursorPosX(ImGui::GetCursorPosX()+ImGui::GetContentRegionAvail().x-72*scale);
            if(ImGui::Button("OK",{72*scale,0}))ImGui::CloseCurrentPopup();
            remember("About OK");ImGui::EndPopup();
        }
        if(saveRequested){ImGui::OpenPopup("Save Snapshot");saveRequested=false;}
        ImGui::SetNextWindowSize({440*scale,0},ImGuiCond_Appearing);
        if(ImGui::BeginPopupModal("Save Snapshot",nullptr,ImGuiWindowFlags_AlwaysAutoResize)){
            ImGui::TextUnformatted("Save the displayed simulation field as PNG");
            ImGui::SetNextItemWidth(-1);ImGui::InputText("##PNG path",snapshotPath,sizeof(snapshotPath));remember("PNG path");
            ImGui::TextDisabled("Full path or a filename in the working directory");
            if(ImGui::Button("Save")){
                std::error_code ec;bool exists=std::filesystem::exists(snapshotPath,ec);
                if(ec)error=ec.message();
                else if(exists)ImGui::OpenPopup("Replace existing file?");
                else if(saveSnapshot())ImGui::CloseCurrentPopup();
            }remember("Save PNG");
            ImGui::SameLine();if(ImGui::Button("Cancel")){exportFrame.reset();ImGui::CloseCurrentPopup();}remember("Cancel PNG");
            bool replaced=false;
            if(ImGui::BeginPopupModal("Replace existing file?",nullptr,ImGuiWindowFlags_AlwaysAutoResize)){
                ImGui::TextUnformatted("A file already exists at this path.");
                if(ImGui::Button("Replace")){replaced=saveSnapshot();ImGui::CloseCurrentPopup();}remember("Replace PNG");
                ImGui::SameLine();if(ImGui::Button("Cancel"))ImGui::CloseCurrentPopup();ImGui::EndPopup();
            }
            if(replaced)ImGui::CloseCurrentPopup();
            if(!error.empty())ImGui::TextWrapped("%s",error.c_str());
            ImGui::EndPopup();
        }
    }
    bool saveSnapshot(){
        try{
            if(!exportFrame)throw std::runtime_error("No displayed field to save.");
            if(!snapshotPath[0])throw std::runtime_error("Enter a PNG filename.");
            renderer.exportPNG(exportFrame,exportDisplay,snapshotPath,1800);
            exportFrame.reset();error.clear();nextUpdate=0;return true;
        }catch(const std::exception& e){error=e.what();return false;}
    }
    void row(const char* label){
        ImGui::AlignTextToFramePadding();ImGui::TextUnformatted(label);
        ImGui::SameLine();ImGui::SetNextItemWidth(ImGui::GetContentRegionAvail().x);
    }
    bool combo(const char* label,int* value,const char* const* choices,int count){
        ImGui::PushID(label);row(label);bool changed=false;
        bool open=ImGui::BeginCombo("##choice",choices[*value]);remember(label);
        if(open){
            for(int i=0;i<count;++i){
                bool selected=*value==i;
                if(ImGui::Selectable(choices[i],selected)){*value=i;changed=true;}
                if(args.selfTest)remember((std::string("Option ")+choices[i]).c_str());
                if(selected)ImGui::SetItemDefaultFocus();
            }
            ImGui::EndCombo();
        }
        ImGui::PopID();return changed;
    }
    void controlsUI(const turbo::ViewState& s){
        // Configuration combos reset immediately; live sliders never restart.
        constexpr int waves[]={2,4,6,8,10,12,16,20};
        constexpr int sizes[]={128,256,512,1024,2048,3072,4096,6144,8192,9216,12288,16384};
        auto numberChoice=[&](const char* label,int& value,const auto& choices){
            row(label);char current[24];std::snprintf(current,sizeof(current),"%d",value);
            ImGui::PushID(label);bool open=ImGui::BeginCombo("##choice",current);remember(label);
            if(open){for(int v:choices){
                std::snprintf(current,sizeof(current),"%d",v);
                bool disabled=label[0]=='N'&&edit.integrator==turbo::Integrator::cnab2&&(v&(v-1));
                ImGui::BeginDisabled(disabled);
                if(ImGui::Selectable(current,v==value)){value=v;configure();}
                remember(current);ImGui::EndDisabled();
            }ImGui::EndCombo();}ImGui::PopID();
        };
        numberChoice("k₀",edit.k0,waves);
        int spectrum=int(edit.startSpectrum);
        const char* spectra[]={"PAO","KM3 (k⁻³)"};
        if(combo("Start spectrum",&spectrum,spectra,2)){edit.startSpectrum=turbo::StartSpectrum(spectrum);configure();}
        row("Seed");ImGui::SetNextItemWidth(ImGui::GetContentRegionAvail().x-30*scale);
        bool seedActive=GImGui->ActiveId==ImGui::GetID("##Seed");
        if(!seedActive)ImGui::PushStyleColor(ImGuiCol_Text,{0,0,0,0});
        if(ImGui::InputText("##Seed",seedText,sizeof(seedText),ImGuiInputTextFlags_EnterReturnsTrue)){
            try{edit.seed=number<uint64_t>(seedText,"Seed");configure();}catch(const std::exception& e){error=e.what();}
        }
        if(!seedActive){
            ImGui::PopStyleColor();auto a=ImGui::GetItemRectMin(),b=ImGui::GetItemRectMax();
            auto* draw=ImGui::GetWindowDrawList();draw->PushClipRect({a.x+5*scale,a.y},{b.x-5*scale,b.y},true);
            presentation::text({b.x-5*scale-ImGui::CalcTextSize(seedText).x,a.y+ImGui::GetStyle().FramePadding.y},seedText);draw->PopClipRect();
        }
        remember("Seed");desktop::hint("Initial condition seed. Enter to apply; Escape to cancel editing.");
        ImGui::SameLine();auto p=ImGui::GetCursorScreenPos();
        if(ImGui::Button("##new seed",{24*scale,24*scale}))action(Action::seed,s);
        remember("New seed");presentation::icon({p.x+12*scale,p.y+12*scale},2,scale);desktop::hint("New initial condition · Ctrl+Shift+R");
        numberChoice("N",edit.n,sizes);
        char value[64];float re=float(s.regulated&&s.stats.reynolds>0?s.stats.reynolds:edit.reynolds);
        std::snprintf(value,sizeof(value),"%.0f",re);ImGui::BeginDisabled(s.regulated);
        if(presentation::slider(s.regulated?"Re · AUTO":"Re · MANUAL",&re,1000,1e6f,value,scale,ImGuiSliderFlags_Logarithmic)){
            edit.reynolds=re;controller->live(edit.cfl,edit.reynolds);
        }remember("Reynolds");ImGui::EndDisabled();desktop::hint("Reynolds number. Live edit without restarting. Ctrl+click for numeric entry.");
        bool regulated=s.regulated;
        if(presentation::toggle("Regulate with Palinstrophy",&regulated,scale)){controller->regulate(regulated);edit=controller->view().config;}
        remember("Regulation");
        ImGui::PushFont(desktop::mono,9);ImGui::TextDisabled("10⁴P/(Z kmax²) ≈ %.2f",10000*s.stats.metric);ImGui::PopFont();
        ImGui::Separator();
        constexpr const char* fields[]={"Enstrophy","Vorticity","Speed"};constexpr int fieldIds[]={2,0,1};
        for(int i=0;i<3;++i){
            if(presentation::choiceButton(fields[i],display.field==fieldIds[i],ImGui::GetContentRegionAvail().x,scale)){
                display.field=fieldIds[i];controller->velocity(display.velocity());nextUpdate=0;
            }remember(fields[i]);
        }
        constexpr const char* palettes[]={"Inferno","Ember","Aurora","Glacier"};constexpr int paletteIds[]={3,1,0,2};
        for(int i=0;i<4;++i){
            if(presentation::choiceButton(palettes[i],display.palette==paletteIds[i],ImGui::GetContentRegionAvail().x,scale,paletteIds[i]))display.palette=paletteIds[i];
            remember(palettes[i]);
        }
        if(display.field==0){constexpr const char* ranges[]={"Sigma","Exposure"};combo("Color range",&display.colorRange,ranges,2);}
        if(display.field==0&&display.colorRange==0){
            std::snprintf(value,sizeof(value),"±%.1fσ",display.sigma);
            presentation::slider("Sigma",&display.sigma,1.5f,3.5f,value,scale);remember("Sigma");
        }else{
            std::snprintf(value,sizeof(value),"%.1f×",display.exposure);
            presentation::slider("Exposure",&display.exposure,.25f,3.f,value,scale);remember("Exposure");
        }
        if(presentation::toggle("Velocity direction",&display.flow,scale)){controller->velocity(display.velocity());nextUpdate=0;}remember("Velocity direction");
        ImGui::Separator();ImGui::PushFont(desktop::mono,9);ImGui::TextDisabled("NUMERICS");ImGui::PopFont();
        const char* methods[]={"IMEX RK3","Integrating-factor RK3","ETD RK4","CNAB2"};int method=int(edit.integrator),form=int(edit.form);
        if(combo("Method",&method,methods,IM_ARRAYSIZE(turbo::methods))){
            edit.integrator=turbo::Integrator(method);
            if(edit.integrator==turbo::Integrator::cnab2){edit.cfl=std::clamp(edit.cfl,.05f,.25f);edit.form=turbo::Form::stress;}
            else {edit.cfl=std::max(edit.cfl,1.f);if(edit.integrator!=turbo::Integrator::etdrk4)edit.cfl=std::min(edit.cfl,1.5f);}
            configure();form=int(edit.form);
        }
        desktop::hint("IF RK3: integrating-factor RK3. CNAB2 requires power-of-two N, seed 1..5010, stress form.");
        const char* forms[]={"Stress","Advective"};ImGui::BeginDisabled(edit.integrator==turbo::Integrator::cnab2);
        if(combo("Form",&form,forms,2)){edit.form=turbo::Form(form);configure();}ImGui::EndDisabled();
        std::snprintf(value,sizeof(value),"%.2f",edit.cfl);
        bool cn=edit.integrator==turbo::Integrator::cnab2;
        if(presentation::slider("CFL",&edit.cfl,cn?.05f:1.f,cn?.25f:edit.integrator==turbo::Integrator::etdrk4?3.f:1.5f,value,scale))controller->live(edit.cfl,edit.reynolds);
        remember("CFL");desktop::hint("Configured CFL target. Live edit; does not restart the flow.");
#ifndef NDEBUG
        ImGui::Spacing();ImGui::Separator();ImGui::PushFont(desktop::mono,9);ImGui::TextDisabled("PRESENTATION");ImGui::PopFont();
        if(presentation::toggle("Show simulation",&visualize,scale))nextUpdate=0;
        remember("Visualization");
        std::snprintf(value,sizeof(value),"%.0f ms",intervalMs);
        if(presentation::slider("Update interval",&intervalMs,4,10000,value,scale,ImGuiSliderFlags_Logarithmic)){
            controller->visualizationInterval(intervalMs/1000.);nextUpdate=0;
        }remember("Update interval");
        ImGui::TextDisabled("Rendering %.1f FPS",ImGui::GetIO().Framerate);
#endif
    }
    void header(const turbo::ViewState& s,float width,float top){
        desktop::mark({45*scale,top+38.5f*scale},21*scale);
        auto* d=ImGui::GetWindowDrawList();d->PushClipRect({83*scale,top},{width-240*scale,top+77*scale},true);
        presentation::title({83*scale,top+12*scale},scale);
        presentation::text({83*scale,top+53*scale},environment.c_str(),14,{1,1,1,1},true);d->PopClipRect();
        d->AddRectFilled({width-214*scale,top+25*scale},{width-106*scale,top+53*scale},IM_COL32(9,38,40,255),14*scale);
        d->AddCircleFilled({width-202*scale,top+39*scale},2.5f*scale,ImGui::GetColorU32(s.ready?presentation::accent:desktop::secondary));
        presentation::text({width-190*scale,top+34*scale},"CUDA / VkFFT",9,presentation::accent,true);
        const Action actions[]={Action::focus,Action::save};const char* names[]={"Focus","Snapshot"};
        for(int i=0;i<2;++i){
            ImVec2 p{width-(92-42*i)*scale,top+25*scale};ImGui::SetCursorScreenPos(p);
            ImGui::BeginDisabled(i==1&&(!shown||exportFrame));
            ImGui::PushID(names[i]);if(ImGui::Button("##icon",{26*scale,26*scale}))action(actions[i],s);ImGui::PopID();remember(names[i]);
            presentation::icon({p.x+13*scale,p.y+13*scale},3+i,scale);ImGui::EndDisabled();
            desktop::hint(i==0?"Focus on flow / Show controls · F":"Save PNG snapshot · Ctrl+S");
        }
    }
    void metrics(const turbo::ViewState& s,float width,float y){
        using presentation::text;presentation::panel({22*scale,y},{width-44*scale,54*scale},scale);
        const char* labels[]={"SIMULATED TIME","INITIAL TURNOVERS","KINETIC ENERGY","ENSTROPHY","TIMESTEP","FPS","STEP","GPU ALLOCATION"};
        // Report real solver + FFT allocation. The renderer has no allocation API;
        // do not fabricate a total from GPU-wide memory usage.
        double mib=double(s.stats.allocatedBytes+s.stats.fftBytes)/1048576.;bool gib=mib>=1024;
        char values[8][40];std::snprintf(values[0],40,"%.3f",s.rate);std::snprintf(values[1],40,"%.3f",s.turnoversRate);
        std::snprintf(values[2],40,"%.3f",s.stats.energy);std::snprintf(values[3],40,"%.3f",s.stats.enstrophy);
        std::snprintf(values[4],40,"%.2e",s.stats.dt);std::snprintf(values[5],40,"%.0f",s.stepsRate);
        std::snprintf(values[6],40,"%llu",static_cast<unsigned long long>(s.stats.step));std::snprintf(values[7],40,gib?"%.1f":"%.0f",gib?mib/1024:mib);
        const char* units[]={"per wall s","per wall s","","","Δt","steps/s","",gib?"GiB":"MiB"};
        float cell=(width-72*scale)/8;
        for(int i=0;i<8;++i){
            float x=36*scale+i*cell;auto* d=ImGui::GetWindowDrawList();d->PushClipRect({x,y},{x+cell-3*scale,y+54*scale},true);
            text({x,y+10*scale},labels[i],7,desktop::secondary,true);
            float size=16;if(i==4&&cell<125*scale)size=13;
            text({x,y+27*scale},s.ready?values[i]:"—",size,i<2?presentation::accent:desktop::foreground,true);
            ImGui::PushFont(desktop::mono,size);float w=ImGui::CalcTextSize(s.ready?values[i]:"—").x;ImGui::PopFont();
            text({x+w+4*scale,y+33*scale},units[i],7,desktop::secondary,true);
            if(i==2&&s.history.size()>1&&cell>w+26*scale){
                auto [low,high]=std::minmax_element(s.history.begin(),s.history.end());
                double span=std::max(*high-*low,std::max(std::abs(*high),1e-12)*.02),mid=(*high+*low)/2;
                for(size_t j=1;j<s.history.size();++j)d->AddLine({x+w+4*scale+24*scale*float(j-1)/(s.history.size()-1),y+34*scale-float((s.history[j-1]-mid)/span)*13*scale},
                    {x+w+4*scale+24*scale*float(j)/(s.history.size()-1),y+34*scale-float((s.history[j]-mid)/span)*13*scale},ImGui::GetColorU32(presentation::accent),scale);
            }
            d->PopClipRect();ImGui::SetCursorScreenPos({x,y});ImGui::InvisibleButton(labels[i],{cell,54*scale});
            if(ImGui::IsItemHovered()){
                ImGui::BeginTooltip();
                if(i==0)ImGui::Text("R_turbo = simulation time / active wall time: %.6f",s.rate);
                else if(i==5)ImGui::Text("Solver: %.3f ms/step · %.1f steps/s\nRendering: %.1f FPS",s.stepsRate>0?1000/s.stepsRate:0,s.stepsRate,ImGui::GetIO().Framerate);
                else if(i==7)ImGui::TextUnformatted("Solver + VkFFT allocation; excludes display textures.");
                else ImGui::Text("%s: %s %s",labels[i],values[i],units[i]);
                ImGui::EndTooltip();
            }
        }
    }

public:
    Application(const Arguments& a,turbo::LinuxDesktopRenderer& r,X11ImGui& p,const cudaDeviceProp& prop)
        :args(a),renderer(r),platform(p),controller(std::make_unique<turbo::Controller>(a.solver,a.device)),
         edit(a.solver.config),display(a.solver.display),visualize(a.visualization),intervalMs(float(a.interval*1000)),gpu(prop.name),focused(a.focus){
        std::snprintf(seedText,sizeof(seedText),"%llu",static_cast<unsigned long long>(edit.seed));
        std::string osName="Linux",osVersion,line;
        std::ifstream release("/etc/os-release");
        if(!release)release.open("/usr/lib/os-release");
        while(std::getline(release,line)){
            auto separator=line.find('=');if(separator==std::string::npos)continue;
            auto key=line.substr(0,separator),value=line.substr(separator+1);
            if(value.size()>=2&&(value.front()=='"'||value.front()=='\'')&&value.back()==value.front())value=value.substr(1,value.size()-2);
            if(key=="NAME"&&!value.empty())osName=value;
            else if(key=="VERSION_ID")osVersion=value;
        }
        if(!osVersion.empty())osName+=" "+osVersion;
        int runtime=0;CUDA_CHECK(cudaRuntimeGetVersion(&runtime));
        char summary[512];std::snprintf(summary,sizeof(summary),"%s · %.1f GiB · CUDA %d.%d · %s",prop.name,double(prop.totalGlobalMem)/1073741824.,runtime/1000,(runtime%1000)/10,osName.c_str());
        environment=summary;
        glRenderer=reinterpret_cast<const char*>(glGetString(GL_RENDERER));
        controller->visualizationInterval(a.interval);controller->visible(visualize);workerVisible=visualize;
    }
    void uiScale(float value){scale=value;}
    double presentationInterval() const {
        const auto& io=ImGui::GetIO();
        if(io.WantTextInput||io.MouseDown[0]||io.MouseDown[1])return 1./60;
        return visualize?std::clamp(double(intervalMs)/1000,1./60,.25):.25;
    }
    bool frame(const turbo::CanvasInfo& geometry){
        auto s=controller->view();double now=turbo::nowSeconds();
        if(releaseEditKey){ImGui::GetIO().AddKeyEvent(editKey,false);ImGui::GetIO().AddKeyEvent(ImGuiMod_Ctrl,false);releaseEditKey=false;}
        if(restoreEditor&&!ImGui::GetIO().MouseDown[0]&&!ImGui::GetIO().MouseClicked[0]){
            ImGui::FocusWindow(editorWindow);ImGui::SetActiveID(editorId,editorWindow);ImGui::SetFocusID(editorId,editorWindow);
            if(editorTemporary)GImGui->TempInputId=editorId;
            restoreEditor=false;editDelay=editKey==ImGuiKey_None?0:2;
        }
        // InputText registers shortcut routing on the frame after activation.
        if(editDelay>0&&--editDelay==0){
            ImGui::GetIO().AddKeyEvent(ImGuiMod_Ctrl,true);ImGui::GetIO().AddKeyEvent(editKey,true);releaseEditKey=true;
        }
        if((args.selfTest||args.benchmarkSeconds)&&!s.error.empty())throw std::runtime_error(s.error);
        if(s.generation!=seenGeneration){seenGeneration=s.generation;shown.reset();texture=0;nextUpdate=0;
            edit=s.config;std::snprintf(seedText,sizeof(seedText),"%llu",static_cast<unsigned long long>(edit.seed));}
        bool visible=visualize&&geometry.visible;
        if(visible!=workerVisible){controller->visible(visible);workerVisible=visible;}
        shortcuts(s);
        ImGui::SetNextWindowPos({0,0});ImGui::SetNextWindowSize(ImGui::GetIO().DisplaySize);
        constexpr ImGuiWindowFlags flags=ImGuiWindowFlags_NoDecoration|ImGuiWindowFlags_NoMove|
            ImGuiWindowFlags_NoSavedSettings|ImGuiWindowFlags_NoBringToFrontOnFocus|
            ImGuiWindowFlags_NoScrollWithMouse|ImGuiWindowFlags_NoScrollbar|ImGuiWindowFlags_MenuBar;
        ImGui::Begin("FastTurbo desktop",nullptr,flags);menus(s);
        float width=ImGui::GetIO().DisplaySize.x,height=ImGui::GetIO().DisplaySize.y;
        float menuHeight=ImGui::GetFrameHeight(),bodyTop=menuHeight+77*scale;
        header(s,width,menuHeight);
        float bodyHeight=std::max(1.f,height-bodyTop-(focused?12:74)*scale);
        float availableWidth=std::max(1.f,width-(focused?44:258)*scale);
        viewportPos={22*scale,bodyTop+34*scale};viewportSize={availableWidth,std::max(1.f,bodyHeight-34*scale)};
        int side=std::max(1,int(std::min(viewportSize.x,viewportSize.y)));
        imagePos={std::floor(viewportPos.x+(viewportSize.x-side)*.5f),std::floor(viewportPos.y+(viewportSize.y-side)*.5f)};
        imageSize={float(side),float(side)};
        ImVec2 panelPos{imagePos.x,imagePos.y-34*scale};
        presentation::panel(panelPos,{float(side),side+34*scale},scale);
        auto* d=ImGui::GetWindowDrawList();
        d->AddCircleFilled({panelPos.x+17*scale,panelPos.y+17*scale},3*scale,ImGui::GetColorU32(s.ready&&s.running?presentation::accent:desktop::secondary));
        const char* names[]={"VORTICITY","SPEED","ENSTROPHY"};
        presentation::text({panelPos.x+34*scale,panelPos.y+12*scale},names[display.field],11,presentation::accent,true);
        char grid[64];std::snprintf(grid,sizeof(grid),"%d² · %d² padded",s.config.n,s.config.m());
        presentation::text({panelPos.x+side-124*scale,panelPos.y+13*scale},grid,9,desktop::secondary,true);
        if(side>370*scale){
            presentation::text({panelPos.x+side-230*scale,panelPos.y+13*scale},display.field==0?"−ω":"0",8,desktop::secondary,true);
            desktop::gradient({panelPos.x+side-212*scale,panelPos.y+16*scale},{54*scale,4*scale},display.palette);
            presentation::text({panelPos.x+side-153*scale,panelPos.y+13*scale},display.field==0?"+ω":display.field==1?"|u|":"ω²",8,desktop::secondary,true);
        }
        int targetSide=std::min(side,4096);
        if(visible&&s.frame&&(now>=nextUpdate||display!=lastDisplay||targetSide!=textureSide)){
            // Never draw velocity from a vorticity-only lease after switching fields.
            if(!display.velocity()||s.frame->hasVelocity){
                turbo::RenderTiming t;
                texture=renderer.renderTexture(s.frame,display,targetSide,t);shown=s.frame;lastDisplay=display;textureSide=targetSide;
                timing.uploads+=t.uploads;timing.deviceCopyBytes+=t.deviceCopyBytes;
                timing.uploadSeconds+=t.uploadSeconds;timing.drawSeconds+=t.drawSeconds;++fieldUpdates;
                nextUpdate=now+intervalMs/1000.;
            }
        }
        if(visualize&&texture)d->AddImage(ImTextureRef(ImTextureID(texture)),imagePos,{imagePos.x+side,imagePos.y+side},{0,1},{1,0});
        else presentation::text({imagePos.x+20*scale,imagePos.y+side*.5f},visualize?"Preparing the flow…":"Visualization disabled",12);
        char seed[80];std::snprintf(seed,sizeof(seed),"UNFORCED · SEED %llu",static_cast<unsigned long long>(s.config.seed));
        ImGui::PushFont(desktop::mono,8);float seedWidth=ImGui::CalcTextSize(seed).x+20*scale;ImGui::PopFont();
        d->AddRectFilled({imagePos.x+14*scale,imagePos.y+14*scale},{imagePos.x+14*scale+seedWidth,imagePos.y+40*scale},IM_COL32(0,0,0,90),13*scale);
        presentation::text({imagePos.x+24*scale,imagePos.y+23*scale},seed,8,desktop::foreground,true);
        float transportY=imagePos.y+side-49*scale;
        d->AddRectFilled({imagePos.x+14*scale,transportY},{imagePos.x+128*scale,transportY+35*scale},IM_COL32(69,48,87,235),18*scale);
        d->AddRect({imagePos.x+14*scale,transportY},{imagePos.x+128*scale,transportY+35*scale},IM_COL32(225,218,242,45),18*scale);
        const char* transport[]={s.running?"Pause":"Run","Single Step","Reset"};const Action actions[]={Action::toggle,Action::step,Action::reset};
        for(int i=0;i<3;++i){
            ImVec2 p{imagePos.x+(14+38*i)*scale,transportY};ImGui::SetCursorScreenPos(p);
            ImGui::PushStyleColor(ImGuiCol_Button,{0,0,0,0});ImGui::PushStyleVar(ImGuiStyleVar_FrameBorderSize,0);
            ImGui::BeginDisabled(!s.ready||args.benchmarkSeconds>0);
            ImGui::PushID(transport[i]);if(ImGui::Button("##transport",{38*scale,35*scale}))action(actions[i],s);ImGui::PopID();remember(transport[i]);
            presentation::icon({p.x+19*scale,p.y+17.5f*scale},i,scale,s.running);ImGui::EndDisabled();
            ImGui::PopStyleVar();ImGui::PopStyleColor();desktop::hint(i==0?"Pause / resume · Space":i==1?"Single step · Right":"Reset the same initial condition · R");
        }
        char time[64];std::snprintf(time,sizeof(time),"t  %.3f",shown?shown->time:s.stats.time);
        ImGui::PushFont(desktop::mono,10);float timeWidth=ImGui::CalcTextSize(time).x+22*scale;ImGui::PopFont();
        float timeX=imagePos.x+side-14*scale-timeWidth;
        d->AddRectFilled({timeX,transportY+3*scale},{imagePos.x+side-14*scale,transportY+32*scale},IM_COL32(0,0,0,95),15*scale);
        presentation::text({timeX+11*scale,transportY+12*scale},time,10,desktop::foreground,true);
        if(!error.empty()||!s.error.empty()){
            ImGui::SetCursorScreenPos({imagePos.x+14*scale,transportY-70*scale});
            ImGui::BeginChild("Message",{side-28*scale,60*scale},ImGuiChildFlags_Borders);
            if(ImGui::SmallButton("Dismiss"))action(Action::dismiss,s);
            remember("Dismiss");
            ImGui::SameLine();ImGui::TextWrapped("%s",error.empty()?s.error.c_str():error.c_str());ImGui::EndChild();
        }
        inspectorPos={width-218*scale,bodyTop};inspectorSize={196*scale,bodyHeight};
        if(!focused){
            ImGui::SetCursorScreenPos(inspectorPos);
            ImGui::BeginChild("Inspector",inspectorSize,ImGuiChildFlags_Borders);
            if(testScroll>=0){ImGui::SetScrollY(testScroll);testScroll=-1;}
            ImGui::PushStyleVar(ImGuiStyleVar_FramePadding,{5*scale,6.5f*scale});
            ImGui::BeginDisabled(args.benchmarkSeconds>0);controlsUI(s);ImGui::EndDisabled();
            ImGui::PopStyleVar();
            inspectorScroll=ImGui::GetScrollY();inspectorMaxScroll=ImGui::GetScrollMaxY();ImGui::EndChild();
            metrics(s,width,height-66*scale);
        }
        dialogs();ImGui::End();
        if(args.selfTest){
            try{selfTest(s,geometry);}
            catch(const std::exception& e){testFailure=e.what();testCapture="failure";return false;}
        }
        if(args.benchmarkSeconds){
            if(s.ready&&!warmupStart)warmupStart=now;
            if(warmupStart&&!measurementStart&&now-warmupStart>=args.solver.guiWarmup){
                controller->startMeasurement();measurementStart=now;timing={};presentations=fieldUpdates=0;
            }
            if(measurementStart&&s.finished&&s.settled){
                completed=true;return false;
            }
            if(now-started>args.benchmarkSeconds+120)throw std::runtime_error("GUI benchmark timed out");
        }
        return true;
    }
    void presented(const turbo::CanvasInfo& geometry){
        ++presentations;
        if(args.selfTest&&!testCapture.empty()){
            auto directory=std::filesystem::path(args.output).parent_path();
            capture((directory/(testCapture+".png")).string(),geometry.width,geometry.height);testCapture.clear();
        }
        if(!captured&&!args.capture.empty()&&(completed||(!args.selfTest&&!args.benchmarkSeconds&&fieldUpdates>3))){
            capture(args.capture,geometry.width,geometry.height);captured=true;
        }
    }
    Json result(){
        auto s=controller->view();
        if(!testFailure.empty())throw std::runtime_error(testFailure);
        if((args.selfTest||args.benchmarkSeconds)&&!completed)throw std::runtime_error("Window closed before validation completed");
        Json result;
        if(args.benchmarkSeconds){
            result=Json::parse(turbo::reportJSON(args.solver,s.initial,s.stats,s.wall,s.gpuSeconds,
                              s.batches,s.snapshots,"native-imgui",s.wall>0?double(presentations)/s.wall:0));
            result["solver"]="FastTurbo native Linux CUDA/VkFFT";
            result["milliseconds_per_step"]=s.stats.step>0?s.wall*1000/double(s.stats.step):0;
            result["warmup_steps"]=nullptr;result["warmup_seconds"]=args.solver.guiWarmup;
            result["timing"]="existing Controller timed run, fixed batch 8; two-second warmup and reset excluded";
        }else result={{"passed",completed},{"checks",checks},{"distinct_fields",fieldHashes.size()}};
        result["gpu"]=gpu;result["opengl_renderer"]=glRenderer;
        result["backend"]="CUDA / VkFFT";result["presentation"]="X11 / GLX (XWayland on Wayland)";
        result["visualization"]=visualize;result["interval_ms"]=intervalMs;
        result["ui"]={{"scale",scale},{"focused",focused},{"font",desktop::fontName},
            {"framebuffer",{ImGui::GetIO().DisplaySize.x,ImGui::GetIO().DisplaySize.y}},
            {"image",{imagePos.x,imagePos.y,imageSize.x,imageSize.y}},
            {"viewport",{viewportPos.x,viewportPos.y,viewportSize.x,viewportSize.y}},
            {"inspector",{inspectorPos.x,inspectorPos.y,inspectorSize.x,inspectorSize.y}},
            {"inspector_scroll_max",inspectorMaxScroll}};
        if(args.selfTest)result["layouts"]=testLayouts;
        result["rendering"]={{"uploads",timing.uploads},{"device_copy_bytes",timing.deviceCopyBytes},
            {"upload_seconds",timing.uploadSeconds},{"draw_submit_seconds",timing.drawSeconds},
            {"field_updates",fieldUpdates},{"presentations",presentations}};
        return result;
    }
};
}
int main(int argc,char** argv){
    try{
        auto args=parse(argc,argv);
        if(args.licenses){std::cout<<fastturboLicenses;return 0;}
        if(args.help){std::cout<<"FastTurbo native Linux - Dear ImGui / CUDA / VkFFT / OpenGL\n"
            "Usage: fastturbo-linux [--resolution N] [--reynolds Re] [--k0 K] [--seed S]\n"
            "  [--cfl C] [--device D] [--paused] [--interval-ms 4..10000] [--no-visualization]\n"
            "  [--benchmark-seconds S] [--self-test] [--output report.json] [--capture window.png]\n"
            "  [--width 760..7680] [--height 560..4320] [--ui-scale 1..3] [--focus]\n"
            "  [--licenses]\n"
            "Use reference-linux/fastturbo_reference or 2dbench for headless execution.\n";return 0;}
        initializeCudaLoader();CUDA_CHECK(cudaSetDevice(args.device));
        cudaDeviceProp prop{};CUDA_CHECK(cudaGetDeviceProperties(&prop,args.device));
        auto renderer=std::unique_ptr<turbo::LinuxDesktopRenderer>(ft_gl_create_desktop(args.device));
        renderer->openDesktop("2DFastTurbo - Native Linux",args.width,args.height);
        ImGuiOwner imgui;
        auto& io=ImGui::GetIO();io.IniFilename=nullptr;io.ConfigFlags|=ImGuiConfigFlags_NavEnableKeyboard;
        presentation::fonts();
        if(!ImGui_ImplOpenGL3_Init("#version 330"))throw std::runtime_error("Dear ImGui OpenGL initialization failed");
        imgui.renderer=true;
        X11ImGui platform(renderer->desktopDisplay(),renderer->desktopWindow());
        float uiScale=args.scale?args.scale:platform.dpiScale();presentation::style(uiScale);
        double nextDpiCheck=0;
        Application app(args,*renderer,platform,prop);app.uiScale(uiScale);
        std::cerr<<"FastTurbo: "<<prop.name<<"; CUDA / VkFFT; "<<glGetString(GL_RENDERER)<<"; X11/GLX\n";
        for(bool running=true;running&&platform.poll();){
            double frameStarted=turbo::nowSeconds();
            if(!args.scale&&frameStarted>=nextDpiCheck){float current=platform.dpiScale();
                if(current!=uiScale){uiScale=current;presentation::style(uiScale);app.uiScale(uiScale);}nextDpiCheck=frameStarted+1;}
            auto geometry=renderer->geometry();geometry.visible=geometry.visible&&!platform.minimized();
            platform.frame(geometry.width,geometry.height);
            ImGui_ImplOpenGL3_NewFrame();ImGui::NewFrame();running=app.frame(geometry);ImGui::Render();
            glBindFramebuffer(GL_FRAMEBUFFER,0);glViewport(0,0,geometry.width,geometry.height);
            glClearColor(.035f,.047f,.075f,1);glClear(GL_COLOR_BUFFER_BIT);
            ImGui_ImplOpenGL3_RenderDrawData(ImGui::GetDrawData());app.presented(geometry);renderer->presentDesktop();
            platform.wait(app.presentationInterval()-(turbo::nowSeconds()-frameStarted));
        }
        if(args.selfTest||args.benchmarkSeconds||!args.output.empty())write(args,app.result());
        return 0;
    }catch(const std::exception& e){std::cerr<<"fastturbo-linux: "<<e.what()<<'\n';return 1;}
}
