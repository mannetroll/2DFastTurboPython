// Native Linux control/presentation host, following the Windows ABI semantics.
#include "fastturbo.h"
#include "ui.hpp"
#include "../cuda/bridge_common.hpp"
#include "controller.hpp"
#include <condition_variable>
#include <dlfcn.h>
#include <functional>
#include <future>
#include <thread>
namespace {
std::thread::id mainThread;
std::string gpuName;
std::filesystem::path resources;
void mainOnly(){if(std::this_thread::get_id()!=mainThread)throw std::runtime_error("UI calls require the main thread.");}
struct UIHandle {
    std::mutex mutex;
    std::condition_variable wake;
    Options options;
    std::shared_ptr<Controller> controller;
    uintptr_t parent=0;
    std::thread renderThread;
    bool stop=false,visible=true,focused=false,exporting=false;
    double exposure=1.2,sigma=2.5,scale=1;
    std::string error;
    uint64_t presentations=0;
    double firstPresentation=0,lastPresentation=0;
    CanvasInfo canvas;
    RenderTiming timing;
    std::vector<std::function<void(LinuxRenderer&)>> jobs;
    ViewState viewLocked() const {
        if(controller)return controller->view();
        ViewState s;s.config=options.config;s.regulated=options.regulated;s.running=!options.paused;return s;
    }
    void startLocked(){
        if(stop)throw std::runtime_error(error.empty()?"Renderer stopped.":error);
        if(!controller){controller=std::make_shared<Controller>(options,0);controller->visible(visible);}
    }
    void configureLocked(Config c){
        auto s=viewLocked();
        if((c.n!=s.config.n||c.k0!=s.config.k0)&&(s.regulated||c.reynolds==s.config.reynolds))c.reynolds=initialReynolds(c.n,c.k0);
        if(c==s.config)return;
        options.config=c;if(controller)controller->configure(c);
    }
    void runningLocked(bool value){
        if(value&&stop)throw std::runtime_error(error.empty()?"Renderer stopped.":error);
        options.paused=!value;if(controller)controller->running(value);
    }
    void resetLocked(){if(controller)controller->reset();}
    Json report(){
        std::lock_guard lock(mutex);auto s=viewLocked();auto d=options.display;
        double re=s.stats.reynolds>0?s.stats.reynolds:s.config.reynolds;
        Json j={{"config",configReport(s.config)},{"ready",s.ready},{"running",s.running},{"settled",s.settled},
            {"generation",s.generation},{"gpu",gpuName},{"backend","CUDA / VkFFT"},
            {"error_message",!error.empty()?Json(error):!s.error.empty()?Json(s.error):Json(nullptr)},
            {"regulated",s.regulated},{"effective_reynolds",re},{"palinstrophy_control",10000*s.stats.metric},
            {"display",fieldNames[d.field]},{"palette",paletteNames[d.palette]},{"color_range",rangeNames[d.colorRange]},
            {"flow",d.flow},{"exposure",exposure},{"sigma",sigma},{"focused",focused},{"exporting",exporting},
            {"elapsed_seconds",s.wall},{"presentations",presentations},{"allocated_bytes",s.stats.allocatedBytes},
            {"history",s.history},{"performance",{{"simtime_per_s",s.rate},{"turnovers_per_s",s.turnoversRate},
                {"steps_per_s",s.stepsRate},{"milliseconds_per_step",s.stepsRate>0?1000/s.stepsRate:0},{"batch_steps",s.batchSteps}}},
            {"rendering",{{"api","CUDA / OpenGL"},{"uploads",timing.uploads},{"device_copy_bytes",timing.deviceCopyBytes},
                {"presentation_seconds",lastPresentation-firstPresentation},
                {"upload_seconds",timing.uploadSeconds},{"draw_submit_seconds",timing.drawSeconds},{"swap_seconds",timing.swapSeconds}}}};
        if(s.ready)j["stats"]=statsReport(s.stats);
        if(s.frame)j["frame"]=frameReport(*s.frame);
        if(parent)j["canvas"]={{"width_points",canvas.width/scale},{"height_points",canvas.height/scale},{"scale",scale},{"visible",visible&&canvas.visible}};
        return j;
    }
    void attach(uintptr_t host){
        if(parent)throw std::runtime_error("Canvas is already attached.");
        if(!host)throw std::runtime_error("Invalid X11 parent window.");
        parent=host;renderThread=std::thread([this]{renderLoop();});
    }
    void renderLoop() noexcept {
        try{
            CUDA_CHECK(cudaSetDevice(0));auto renderer=linuxRenderer(parent);
            uint64_t seenGeneration=0;double nextPresent=0;
            for(;;){
                ViewState s;Display display;bool draw;double dpi;
                std::vector<std::function<void(LinuxRenderer&)>> pending;
                {
                    std::unique_lock lock(mutex);if(stop)return;
                    s=viewLocked();display=options.display;draw=visible;dpi=scale;pending.swap(jobs);
                    if(s.generation!=seenGeneration){seenGeneration=s.generation;presentations=0;timing={};firstPresentation=lastPresentation=0;}
                }
                for(auto& job:pending)job(*renderer);
                auto geometry=renderer->geometry();
                {std::lock_guard lock(mutex);canvas=geometry;}
                if(int action=renderer->event()){
                    std::lock_guard lock(mutex);
                    if(controller&&controller->view().ready){
                        if(action==1)runningLocked(!controller->view().running);
                        else if(action==2){options.paused=true;controller->singleStep();}
                        else if(action==3)resetLocked();
                    }
                }
                if(draw&&geometry.visible&&nowSeconds()>=nextPresent){
                    auto t=renderer->present(s.frame,display,s.config.seed,s.stats.time,s.running,dpi);
                    double completed=nowSeconds();if(nextPresent==0)nextPresent=completed;
                    nextPresent+=(std::floor(std::max(0.,completed-nextPresent)*60)+1)/60;
                    std::lock_guard lock(mutex);
                    if(viewLocked().generation==s.generation){
                        if(s.frame){if(!presentations)firstPresentation=completed;lastPresentation=completed;++presentations;}
                        timing.uploads+=t.uploads;timing.deviceCopyBytes+=t.deviceCopyBytes;
                        timing.uploadSeconds+=t.uploadSeconds;timing.drawSeconds+=t.drawSeconds;timing.swapSeconds+=t.swapSeconds;
                    }
                }
                double delay=nextPresent>nowSeconds()?std::min(.016,nextPresent-nowSeconds()):.016;
                std::unique_lock lock(mutex);wake.wait_for(lock,std::chrono::duration<double>(delay),[&]{return stop||!jobs.empty();});
            }
        }catch(const std::exception& e){
            std::lock_guard lock(mutex);error=e.what();stop=true;options.paused=true;
            if(controller)controller->running(false);
            jobs.clear();wake.notify_all();
        }
    }
    void exportFrame(const Json& j){
        auto promise=std::make_shared<std::promise<void>>();auto future=promise->get_future();
        {
            std::lock_guard lock(mutex);auto frame=viewLocked().frame;auto display=options.display;
            if(!frame)throw std::runtime_error("No completed frame.");
            if(stop||!parent)throw std::runtime_error(error.empty()?"No native renderer.":error);
            exporting=true;
            jobs.push_back([frame,display,j,promise](LinuxRenderer& renderer){
                try{exportFields(*frame,j);if(has(j,"png"))renderer.exportPNG(frame,display,path(j,"png"),pixelSize(j));promise->set_value();}
                catch(...){promise->set_exception(std::current_exception());}
            });wake.notify_one();
        }
        try{
            while(future.wait_for(std::chrono::milliseconds(50))!=std::future_status::ready){std::lock_guard lock(mutex);if(stop)throw std::runtime_error(error.empty()?"Renderer stopped.":error);}
            future.get();
        }catch(...){std::lock_guard lock(mutex);exporting=false;throw;}
        std::lock_guard lock(mutex);exporting=false;
    }
    ~UIHandle(){
        {std::lock_guard lock(mutex);stop=true;wake.notify_one();}
        if(renderThread.joinable())renderThread.join();
        controller.reset();
    }
};
}
void initializeLinuxUI(const char* directory,const std::string& gpu){mainThread=std::this_thread::get_id();gpuName=gpu;resources=directory?directory:"";}
bool linuxRenderingBuilt(){return FASTTURBO_HAS_RENDERER;}
std::unique_ptr<LinuxRenderer> linuxRenderer(uintptr_t parent){
    if(!linuxRenderingBuilt())throw std::runtime_error("GPU rendering was not built; install OpenGL/X11/libpng development files and rebuild, or use --headless.");
    static CreateRenderer create=[] {
        auto file=resources/"libfastturbo_gl.so";void* module=dlopen(file.c_str(),RTLD_NOW|RTLD_LOCAL);
        if(!module)throw std::runtime_error(std::string("Cannot load Linux renderer: ")+dlerror());
        auto function=reinterpret_cast<CreateRenderer>(dlsym(module,"ft_gl_create"));
        if(!function)throw std::runtime_error("Incompatible Linux rendering module.");
        return function; // Retain the module for all frame/controller lifetimes.
    }();
    return std::unique_ptr<LinuxRenderer>(create(parent,0));
}

extern "C" {
void* ft_ui_create(){try{mainOnly();return new UIHandle;}catch(...){return nullptr;}}
void ft_ui_destroy(void* handle){if(handle&&std::this_thread::get_id()==mainThread)delete static_cast<UIHandle*>(handle);}
char* ft_ui_attach(void* handle,void* parent){return response([&]{mainOnly();if(!handle)throw std::runtime_error("Closed UI.");static_cast<UIHandle*>(handle)->attach(reinterpret_cast<uintptr_t>(parent));return Json::object();});}
char* ft_ui_call(void* handle,const char* input) {
    return response([&]()->Json{
        mainOnly();if(!handle)throw std::runtime_error("Closed UI.");auto& h=*static_cast<UIHandle*>(handle);
        auto j=decode(input);auto op=j.value("op","");
        if(op=="export")h.exportFrame(j);
        else if(op=="save")throw std::runtime_error("Use the shared application Save dialog."); else {
            std::lock_guard lock(h.mutex);auto s=h.viewLocked();
            if(op=="get"||op=="flush"){}
            else if(op=="start")h.startLocked();
            else if(op=="toggle")h.runningLocked(!s.running);
            else if(op=="running")h.runningLocked(j.value("value",false));
            else if(op=="step"){
                if(h.stop)throw std::runtime_error(h.error.empty()?"Renderer stopped.":h.error);
                h.options.paused=true;if(h.controller)h.controller->singleStep();
            }
            else if(op=="reset")h.resetLocked();
            else if(op=="seed"){auto c=s.config;++c.seed;h.configureLocked(c);}
            else if(op=="visible"){h.visible=j.value("value",true);if(h.controller)h.controller->visible(h.visible);h.wake.notify_one();}
            else if(op=="dismiss"){h.error.clear();if(h.controller)h.controller->dismissError();}
            else if(op=="configure"){
                auto c=configuration(j,s.config);
                if(j.value("cap_cfl",false)&&c.integrator!=s.config.integrator&&c.integrator!=Integrator::etdrk4)c.cfl=std::min(c.cfl,1.5f);
                h.configureLocked(c);
            } else if(op=="set"){
                if(has(j,"regulated")){h.options.regulated=j.at("regulated").get<bool>();if(h.controller)h.controller->regulate(h.options.regulated);s=h.viewLocked();}
                if(has(j,"cfl")||has(j,"log_reynolds")){
                    float cfl=float(std::clamp(scalar(j,"cfl",s.config.cfl),1.,3.));
                    double re=has(j,"log_reynolds")?std::pow(10.,std::clamp(scalar(j,"log_reynolds",0),3.,6.)):s.config.reynolds;
                    if(h.controller)h.controller->live(cfl,re);
                    h.options.config.cfl=cfl;if(!h.options.regulated)h.options.config.reynolds=re;
                }
                h.options.display=displayOptions(j,h.options.display);
                if(has(j,"canvas_scale"))h.scale=std::clamp(scalar(j,"canvas_scale",1),.5,8.);
                h.sigma=std::clamp(scalar(j,"sigma",h.sigma),1.5,3.5);h.exposure=std::clamp(scalar(j,"exposure",h.exposure),.25,3.);
                if(h.controller)h.controller->velocity(h.options.display.velocity());
                if(has(j,"focused"))h.focused=j.at("focused").get<bool>();
            } else if(op=="benchmark"){
                auto next=h.options;
                if(j.contains("until"))next.until=has(j,"until")?std::optional<double>(scalar(j,"until",0)):std::nullopt;
                if(j.contains("wall_seconds"))next.wallSeconds=scalar(j,"wall_seconds",0);
                if(j.contains("batch")){next.fixedBatch=has(j,"batch");next.batch=j.value("batch",Json(8)).is_null()?8:j.at("batch").get<int>();}
                if(next.batch<1||next.batch>32||next.wallSeconds<0||(next.until&&*next.until<0))throw std::invalid_argument("Invalid benchmark limits.");
                if(j.contains("initial"))next.initial=path(j,"initial");
                if(j.contains("spectrum"))next.spectrum=path(j,"spectrum");
                h.options=next;if(h.controller)h.controller->benchmark(next);
            } else throw std::invalid_argument("Unknown UI operation.");
        }
        return h.report();
    });
}
}
