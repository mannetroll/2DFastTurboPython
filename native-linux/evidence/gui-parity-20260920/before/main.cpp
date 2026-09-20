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
#include "json.hpp"
#include <png.h>
#include <charconv>
#include <chrono>
#include <fstream>
#include <iostream>
#include <map>
#include <set>

namespace {
using Json=nlohmann::json;
struct Arguments {
    turbo::Options solver;
    int device=0;
    double interval=.1,benchmarkSeconds=0;
    bool visualization=true,selfTest=false,help=false;
    std::string output,capture;
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
        else if(key=="--resolution")a.solver.config.n=number<int>(value(),key);
        else if(key=="--reynolds"){a.solver.config.reynolds=number<double>(value(),key);explicitRe=true;}
        else if(key=="--k0")a.solver.config.k0=number<int>(value(),key);
        else if(key=="--cfl")a.solver.config.cfl=number<float>(value(),key);
        else if(key=="--seed")a.solver.config.seed=number<uint64_t>(value(),key);
        else if(key=="--device")a.device=number<int>(value(),key);
        else if(key=="--interval-ms")a.interval=number<double>(value(),key)/1000;
        else if(key=="--benchmark-seconds"){
            a.benchmarkSeconds=number<double>(value(),key);
            if(a.benchmarkSeconds<=0)throw std::invalid_argument("Benchmark seconds must be positive.");
        }
        else if(key=="--no-visualization")a.visualization=false;
        else if(key=="--paused")a.solver.paused=true;
        else if(key=="--self-test")a.selfTest=true;
        else if(key=="--output")a.output=value();
        else if(key=="--capture")a.capture=value();
        else throw std::invalid_argument("Unknown option: "+key);
    }
    if(a.help)return a;
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
    void remember(const char* name){auto lo=ImGui::GetItemRectMin(),hi=ImGui::GetItemRectMax();controls[name]={(lo.x+hi.x)/2,(lo.y+hi.y)/2};}
    bool button(const char* name){bool pressed=ImGui::Button(name);remember(name);return pressed;}
    void click(const char* name){auto p=controls.at(name);platform.click(p.x,p.y);}
    void check(bool condition,const char* message){if(!condition)throw std::runtime_error(std::string("Self-test: ")+message);}
    void next(const char* message){checks.push_back(message);++stage;stageTime=turbo::nowSeconds();}
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
    void selfTest(const turbo::ViewState& s,const turbo::CanvasInfo& geometry){
        double now=turbo::nowSeconds();
        if(now-stageTime>30)throw std::runtime_error("Self-test timeout at stage "+std::to_string(stage));
        if(!s.ready||!shown||fieldUpdates<1)return;
        switch(stage){
        case 0: if(s.settled){previousHash=pixels();click("Run");next("CUDA/VkFFT initialization and GPU texture");}break;
        case 1: if(s.running&&s.stats.step>=8){click("Pause");next("Run button");}break;
        case 2: if(!s.running&&s.settled){pausedStep=s.stats.step;next("Pause button settled");}break;
        case 3: if(now-stageTime>.3){check(s.stats.step==pausedStep,"paused simulation advanced");click("Single Step");next("Pause remains stable");}break;
        case 4: if(s.settled&&s.stats.step==pausedStep+1){click("Reset");next("Single Step exactly one step");}break;
        case 5: if(s.settled&&s.stats.step==0&&shown->time==0){
            check(pixels()==previousHash,"reset did not reproduce initial pixels");fieldHashes.insert(pixels());
            click("Velocity magnitude");next("Reset reproduces seed and pixels");}break;
        case 6: if(display.field==1&&lastDisplay==display&&shown->hasVelocity){fieldHashes.insert(pixels());click("Enstrophy");next("Velocity magnitude texture");}break;
        case 7: if(display.field==2&&lastDisplay==display){fieldHashes.insert(pixels());click("Vorticity");next("Enstrophy texture");}break;
        case 8: if(display.field==0&&lastDisplay==display){
            check(fieldHashes.size()==3,"field switching produced identical images");
            platform.resize(1180,820);next("Three distinct field visualizations");}break;
        case 9: if(geometry.width==1180&&geometry.height==820){platform.resize(980,740);next("Window resize larger");}break;
        case 10: if(geometry.width==980&&geometry.height==740){click("Visualization");next("Window resize smaller");}break;
        case 11: if(!visualize){click("Run");next("Visualization disabled");}break;
        case 12: if(s.running&&s.stats.step>8){click("Pause");next("Solver advances without visualization");}break;
        case 13: if(s.settled&&!s.running){click("Visualization");next("Pause without visualization");}break;
        case 14: if(visualize&&shown&&shown->time==s.stats.time){pausedStep=s.stats.step;click("Run");next("Visualization restored while paused");}break;
        case 15: if(s.running&&s.stats.step>pausedStep){completed=true;next("Close requested while solver is running");platform.close();}break;
        }
    }
    void controlsUI(const turbo::ViewState& s){
        ImGui::TextUnformatted("FastTurbo");ImGui::TextDisabled("Native Linux / CUDA + VkFFT");ImGui::Separator();
        if(button(s.running?"Pause":"Run"))controller->running(!s.running);
        ImGui::SameLine();if(button("Single Step"))controller->singleStep();
        ImGui::SameLine();if(button("Reset"))controller->reset();
        ImGui::TextDisabled("Space: run/pause   Right: step");
        ImGui::TextDisabled("R: reset   Ctrl+Q: quit");
        ImGui::Spacing();ImGui::SeparatorText("Simulation");
        ImGui::SetNextItemWidth(160);ImGui::InputInt("Resolution N",&edit.n,128,1024);
        ImGui::SetNextItemWidth(160);ImGui::InputDouble("Reynolds",&edit.reynolds,0,0,"%.6g");
        ImGui::SetNextItemWidth(160);ImGui::InputFloat("CFL",&edit.cfl,.1f,1.f,"%.3f");
        ImGui::SetNextItemWidth(160);ImGui::InputInt("K0",&edit.k0);
        ImGui::SetNextItemWidth(160);ImGui::InputScalar("Seed",ImGuiDataType_U64,&edit.seed);
        int method=int(edit.integrator),form=int(edit.form),spectrum=int(edit.startSpectrum);
        ImGui::SetNextItemWidth(160);if(ImGui::Combo("Method",&method,turbo::methods,IM_ARRAYSIZE(turbo::methods))){
            edit.integrator=turbo::Integrator(method);
            if(edit.integrator==turbo::Integrator::cnab2){edit.cfl=std::clamp(edit.cfl,.05f,.25f);edit.form=turbo::Form::stress;form=0;}
            else {edit.cfl=std::max(edit.cfl,1.f);if(edit.integrator!=turbo::Integrator::etdrk4)edit.cfl=std::min(edit.cfl,1.5f);}
        }
        ImGui::SetNextItemWidth(160);if(ImGui::Combo("Form",&form,turbo::forms,IM_ARRAYSIZE(turbo::forms)))edit.form=turbo::Form(form);
        ImGui::SetNextItemWidth(160);if(ImGui::Combo("Start spectrum",&spectrum,turbo::startSpectra,2))edit.startSpectrum=turbo::StartSpectrum(spectrum);
        if(button("Apply and Reset")){
            try{
                if(!std::isfinite(edit.reynolds))throw std::invalid_argument("Reynolds must be finite.");
                edit.validate();controller->configure(edit);edit=controller->view().config;error.clear();
            }catch(const std::exception& e){error=e.what();}
        }
        if(edit.integrator==turbo::Integrator::cnab2)ImGui::TextWrapped("CNAB2: power-of-two N, seed 1..5010, stress form.");
        bool regulated=s.regulated;
        if(ImGui::Checkbox("Regulate with palinstrophy",&regulated))controller->regulate(regulated);
        ImGui::SeparatorText("Visualization");
        bool changed=ImGui::Checkbox("Visualization",&visualize);remember("Visualization");
        if(changed)nextUpdate=0;
        ImGui::SetNextItemWidth(140);
        if(ImGui::SliderFloat("Update interval (ms)",&intervalMs,4,10000,"%.0f",ImGuiSliderFlags_Logarithmic)){
            controller->visualizationInterval(intervalMs/1000.);nextUpdate=0;
        }
        constexpr const char* fields[]={"Vorticity","Velocity magnitude","Enstrophy"};
        for(int i=0;i<3;++i){if(ImGui::RadioButton(fields[i],display.field==i)){display.field=i;controller->velocity(display.velocity());nextUpdate=0;}remember(fields[i]);}
        constexpr const char* palettes[]={"Aurora","Ember","Glacier","Inferno"};
        ImGui::SetNextItemWidth(160);ImGui::Combo("Palette",&display.palette,palettes,4);
        constexpr const char* ranges[]={"Mean / sigma","Full range"};
        ImGui::SetNextItemWidth(160);ImGui::Combo("Vorticity range",&display.colorRange,ranges,2);
        ImGui::SliderFloat("Exposure",&display.exposure,.02f,5.f,"%.2f");
        ImGui::SliderFloat("Sigma",&display.sigma,.2f,8.f,"%.2f");
        if(ImGui::Checkbox("Flow overlay",&display.flow)){controller->velocity(display.velocity());nextUpdate=0;}
        if(!error.empty()||!s.error.empty()){
            ImGui::PushStyleColor(ImGuiCol_Text,ImVec4(1,.45f,.3f,1));
            ImGui::TextWrapped("%s",error.empty()?s.error.c_str():error.c_str());ImGui::PopStyleColor();
        }
    }
public:
    Application(const Arguments& a,turbo::LinuxDesktopRenderer& r,X11ImGui& p,const char* name)
        :args(a),renderer(r),platform(p),controller(std::make_unique<turbo::Controller>(a.solver,a.device)),
         edit(a.solver.config),display(a.solver.display),visualize(a.visualization),intervalMs(float(a.interval*1000)),gpu(name){
        glRenderer=reinterpret_cast<const char*>(glGetString(GL_RENDERER));
        controller->visualizationInterval(a.interval);controller->visible(visualize);workerVisible=visualize;
    }
    double presentationInterval() const {
        const auto& io=ImGui::GetIO();
        if(io.WantTextInput||io.MouseDown[0]||io.MouseDown[1])return 1./60;
        return visualize?std::clamp(double(intervalMs)/1000,1./60,.25):.25;
    }
    bool frame(const turbo::CanvasInfo& geometry){
        auto s=controller->view();double now=turbo::nowSeconds();
        if((args.selfTest||args.benchmarkSeconds)&&!s.error.empty())throw std::runtime_error(s.error);
        if(s.generation!=seenGeneration){seenGeneration=s.generation;shown.reset();texture=0;nextUpdate=0;}
        bool visible=visualize&&geometry.visible;
        if(visible!=workerVisible){controller->visible(visible);workerVisible=visible;}
        if(!ImGui::GetIO().WantTextInput){
            if(ImGui::IsKeyPressed(ImGuiKey_Space,false))controller->running(!s.running);
            if(ImGui::IsKeyPressed(ImGuiKey_RightArrow,false))controller->singleStep();
            if(ImGui::IsKeyPressed(ImGuiKey_R,false))controller->reset();
            if(ImGui::GetIO().KeyCtrl&&ImGui::IsKeyPressed(ImGuiKey_Q,false))platform.close();
        }
        ImGui::SetNextWindowPos({0,0});ImGui::SetNextWindowSize(ImGui::GetIO().DisplaySize);
        constexpr ImGuiWindowFlags flags=ImGuiWindowFlags_NoDecoration|ImGuiWindowFlags_NoMove|
            ImGuiWindowFlags_NoSavedSettings|ImGuiWindowFlags_NoBringToFrontOnFocus;
        ImGui::Begin("FastTurbo desktop",nullptr,flags);
        ImGui::BeginChild("Controls",{340,0},ImGuiChildFlags_Borders);
        ImGui::BeginDisabled(args.benchmarkSeconds>0);controlsUI(s);ImGui::EndDisabled();ImGui::EndChild();
        ImGui::SameLine();ImGui::BeginChild("Field and statistics");
        ImGui::Text("%s",gpu.c_str());
        ImGui::Text("N %d -> %d   %s / %s   seed %llu",s.config.n,s.config.m(),turbo::methods[int(s.config.integrator)],
                    turbo::startSpectra[int(s.config.startSpectrum)],static_cast<unsigned long long>(s.config.seed));
        ImGui::Text("Iteration %llu   T %.6f   DT %.7g",static_cast<unsigned long long>(s.stats.step),s.stats.time,s.stats.dt);
        ImGui::Text("%.3f ms/step    %.1f steps/s    R_turbo %.4f",s.stepsRate>0?1000/s.stepsRate:0,s.stepsRate,s.rate);
        ImGui::Text("CFL target %.3g    Re %.6g    Batch %d",s.config.cfl,s.stats.reynolds,args.solver.fixedBatch?args.solver.batch:s.batchSteps);
        ImGui::Text("Rendering %.1f FPS    Field updates %llu",ImGui::GetIO().Framerate,static_cast<unsigned long long>(fieldUpdates));
        ImGui::TextDisabled("%s",!s.error.empty()?"Solver error":!s.ready?"Initializing CUDA / VkFFT...":s.running?"Running":s.settled?"Paused":"Finishing current batch...");
        auto space=ImGui::GetContentRegionAvail();int side=std::max(1,int(std::min(space.x,space.y)));
        if(visible&&s.frame&&(now>=nextUpdate||display!=lastDisplay||side!=textureSide)){
            // Never draw velocity from a vorticity-only lease after switching fields.
            if(!display.velocity()||s.frame->hasVelocity){
                turbo::RenderTiming t;
                texture=renderer.renderTexture(s.frame,display,side,t);shown=s.frame;lastDisplay=display;textureSide=side;
                timing.uploads+=t.uploads;timing.deviceCopyBytes+=t.deviceCopyBytes;
                timing.uploadSeconds+=t.uploadSeconds;timing.drawSeconds+=t.drawSeconds;++fieldUpdates;
                nextUpdate=now+intervalMs/1000.;
            }
        }
        if(visualize&&texture)ImGui::Image(ImTextureRef(ImTextureID(texture)),{float(side),float(side)},{0,1},{1,0});
        else ImGui::TextUnformatted(visualize?"Waiting for a GPU field...":"Visualization disabled");
        ImGui::EndChild();ImGui::End();
        if(args.selfTest)selfTest(s,geometry);
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
        if(!captured&&!args.capture.empty()&&(completed||(!args.selfTest&&!args.benchmarkSeconds&&fieldUpdates>3))){
            capture(args.capture,geometry.width,geometry.height);captured=true;
        }
    }
    Json result(){
        auto s=controller->view();
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
        if(args.help){std::cout<<"FastTurbo native Linux - Dear ImGui / CUDA / VkFFT / OpenGL\n"
            "Usage: fastturbo-linux [--resolution N] [--reynolds Re] [--k0 K] [--seed S]\n"
            "  [--cfl C] [--device D] [--paused] [--interval-ms 4..10000] [--no-visualization]\n"
            "  [--benchmark-seconds S] [--self-test] [--output report.json] [--capture window.png]\n"
            "Use reference-linux/fastturbo_reference or 2dbench for headless execution.\n";return 0;}
        initializeCudaLoader();CUDA_CHECK(cudaSetDevice(args.device));
        cudaDeviceProp prop{};CUDA_CHECK(cudaGetDeviceProperties(&prop,args.device));
        auto renderer=std::unique_ptr<turbo::LinuxDesktopRenderer>(ft_gl_create_desktop(args.device));
        renderer->openDesktop("FastTurbo - Native Linux",1120,800);
        ImGuiOwner imgui;
        auto& io=ImGui::GetIO();io.IniFilename=nullptr;io.ConfigFlags|=ImGuiConfigFlags_NavEnableKeyboard;
        io.Fonts->AddFontDefault();io.FontGlobalScale=1.05f;
        ImGui::StyleColorsDark();auto& style=ImGui::GetStyle();style.WindowPadding={12,12};
        style.FrameRounding=4;style.ChildRounding=6;style.FramePadding={6,4};
        if(!ImGui_ImplOpenGL3_Init("#version 330"))throw std::runtime_error("Dear ImGui OpenGL initialization failed");
        imgui.renderer=true;
        X11ImGui platform(renderer->desktopDisplay(),renderer->desktopWindow());
        Application app(args,*renderer,platform,prop.name);
        std::cerr<<"FastTurbo: "<<prop.name<<"; CUDA / VkFFT; "<<glGetString(GL_RENDERER)<<"; X11/GLX\n";
        for(bool running=true;running&&platform.poll();){
            double frameStarted=turbo::nowSeconds();
            auto geometry=renderer->geometry();platform.frame(geometry.width,geometry.height);
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
