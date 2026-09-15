// ABI adapter only. Numerical code and D3D shaders come from reference-windows.
#include "fastturbo.h"
#include "controller.hpp"
#include "renderer.hpp"
#include "cuda_support.cuh"
#include <cuda_d3d11_interop.h>
#include "json.hpp"
#include <commdlg.h>
#include <mmsystem.h>
#include <windowsx.h>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <fstream>
#include <functional>
#include <future>
#include <mutex>
#include <sstream>

using namespace turbo;
using Json = nlohmann::json;
namespace {
DWORD mainThread = 0;
int deviceId = 0;
std::string gpuName;
constexpr const char* fieldNames[] = {"vorticity", "speed", "enstrophy"};
constexpr const char* paletteNames[] = {"aurora", "ember", "glacier", "inferno"};
constexpr const char* rangeNames[] = {"sigma", "exposure"};

void mainOnly() {
    if (GetCurrentThreadId() != mainThread) throw std::runtime_error("UI calls require the main thread.");
}
char* copyString(const std::string& value) {
    auto result = static_cast<char*>(std::malloc(value.size()+1));
    if (result) std::memcpy(result, value.c_str(), value.size()+1);
    return result;
}
template<class F> char* response(F&& action) noexcept {
    try { return copyString(action().dump()); }
    catch (const std::exception& e) { return copyString(Json{{"error", e.what()}}.dump()); }
    catch (...) { return copyString("{\"error\":\"Unknown native failure\"}"); }
}
Json decode(const char* value) {
    if (!value) throw std::invalid_argument("Expected a JSON object.");
    auto result = Json::parse(value);
    if (!result.is_object()) throw std::invalid_argument("Expected a JSON object.");
    return result;
}
bool has(const Json& j, const char* key) { return j.contains(key) && !j.at(key).is_null(); }
std::filesystem::path path(const Json& j, const char* key) {
    if(!has(j,key))return {};
    auto value=j.at(key).get<std::string>();
    return std::filesystem::path(std::u8string(value.begin(),value.end()));
}
template<size_t N> int choice(const Json& j, const char* key, const char* const (&names)[N], int fallback) {
    if (!has(j,key)) return fallback;
    auto value = j.at(key).get<std::string>();
    for (int i=0;i<int(N);++i) if (value == names[i]) return i;
    throw std::invalid_argument(std::string("Unknown ")+key+": "+value);
}
double scalar(const Json& j, const char* key, double fallback) {
    double value = has(j,key) ? j.at(key).get<double>() : fallback;
    if (!std::isfinite(value)) throw std::invalid_argument(std::string("Non-finite ")+key);
    return value;
}
Config configuration(const Json& j, Config c={}) {
    if (has(j,"resolution")) c.n=j.at("resolution").get<int>();
    if (has(j,"k0")) c.k0=j.at("k0").get<int>();
    if (has(j,"seed")) {
        auto s=j.at("seed").is_string()?j.at("seed").get<std::string>():j.at("seed").dump();
        if (s.empty() || s.find_first_not_of("0123456789")!=std::string::npos) throw std::invalid_argument("Seed must be UInt64.");
        size_t used=0;c.seed=std::stoull(s,&used);
        if (used!=s.size()) throw std::invalid_argument("Seed must be UInt64.");
    }
    c.reynolds=scalar(j,"reynolds",c.reynolds);
    c.cfl=float(scalar(j,"cfl",c.cfl));
    c.integrator=Integrator(choice(j,"integrator",methods,int(c.integrator)));
    c.form=Form(choice(j,"operator",forms,int(c.form)));
    if (j.contains("fixed_dt")) {
        c.fixedDt=has(j,"fixed_dt")?float(scalar(j,"fixed_dt",0)):0;
        if (has(j,"fixed_dt") && !(c.fixedDt>0)) throw std::invalid_argument("Fixed timestep must be positive.");
    }
    c.validate();
    if (c.k0>1000000) throw std::invalid_argument("Initial wavenumber is too large.");
    return c;
}
Json configReport(const Config& c) {
    return {{"resolution",c.n},{"padded_resolution",c.m()},{"reynolds",c.reynolds},{"viscosity",float(1/c.reynolds)},
        {"k0",c.k0},{"seed",std::to_string(c.seed)},{"cfl",c.cfl},{"integrator",methods[int(c.integrator)]},
        {"operator",forms[int(c.form)]},{"fixed_dt",c.fixedDt>0?Json(c.fixedDt):Json(nullptr)}};
}
Json statsReport(const Statistics& s) {
    return {{"steps",s.step},{"simulation_time",s.time},{"dt",s.dt},{"energy",s.energy},{"enstrophy",s.enstrophy},
        {"palinstrophy",s.palinstrophy},{"palinstrophy_ratio",s.metric},{"reynolds",s.reynolds},
        {"initial_turnover_time",s.turnover},{"gpu_milliseconds",s.gpuMilliseconds},{"allocated_bytes",s.allocatedBytes},
        {"is_finite",std::isfinite(s.time)&&std::isfinite(s.energy)&&std::isfinite(s.enstrophy)&&std::isfinite(s.palinstrophy)}};
}
Json frameReport(const Frame& f) {
    return {{"sequence",f.sequence},{"time",f.time},{"grid_size",f.n},{"max_vorticity",f.maxOmega},
        {"mean_vorticity",f.mean},{"std_vorticity",f.sigma},{"max_speed",f.maxSpeed}};
}
template<class T> void writeFile(const std::filesystem::path& name, const std::vector<T>& values) {
    std::ofstream out(name,std::ios::binary);
    if (!out.write(reinterpret_cast<const char*>(values.data()),std::streamsize(values.size()*sizeof(T))))
        throw std::runtime_error("Cannot write export file.");
}
void exportFields(const Frame& f, const Json& j) {
    if (has(j,"fields")) {
        std::vector<float> data(size_t(3)*f.n*f.n);
        CUDA_CHECK(cudaMemcpy(data.data(),f.data(),data.size()*sizeof(float),cudaMemcpyDeviceToHost));
        writeFile(path(j,"fields"),data); // Only explicit validation/export requests read back fields.
    }
}
Display displayOptions(const Json& j, Display d={}) {
    d.field=choice(j,"display",fieldNames,d.field);d.palette=choice(j,"palette",paletteNames,d.palette);
    d.colorRange=choice(j,"color_range",rangeNames,d.colorRange);
    d.exposure=float(std::clamp(scalar(j,"exposure",d.exposure),.25,3.));
    d.sigma=float(std::clamp(scalar(j,"sigma",d.sigma),1.5,3.5));
    if (has(j,"flow")) d.flow=j.at("flow").get<bool>();
    return d;
}
int pixelSize(const Json& j) {
    int size=j.value("pixel_size",1800);
    if (size<1 || size>8192) throw std::invalid_argument("PNG size must be 1 through 8192.");
    return size;
}
struct COMScope {
    HRESULT result=CoInitializeEx(nullptr,COINIT_MULTITHREADED);
    COMScope(){if(FAILED(result)&&result!=RPC_E_CHANGED_MODE)hrCheck(result,"Initialize COM");}
    ~COMScope(){if(SUCCEEDED(result))CoUninitialize();}
};
struct EngineHandle {
    std::mutex mutex;
    Solver core;
    explicit EngineHandle(const Json& j):core(configuration(j),loadInitial([&]{Options o;o.config=configuration(j);o.initial=path(j,"initial");return o;}())){}
};

struct UIHandle {
    std::mutex mutex;
    std::condition_variable wake;
    Options options;
    std::shared_ptr<Controller> controller;
    HWND parent=nullptr, child=nullptr;
    std::thread renderThread;
    bool stop=false, visible=true, focused=false, exporting=false;
    double exposure=1.2, sigma=2.5;
    std::string error;
    uint64_t presentations=0;
    std::vector<std::function<void(Renderer&)>> jobs;

    ViewState viewLocked() const {
        if (controller) return controller->view();
        ViewState s;s.config=options.config;s.regulated=options.regulated;s.running=!options.paused;return s;
    }
    void startLocked() {
        if (!controller) {
            controller=std::make_shared<Controller>(options,deviceId);
            controller->visible(visible);
        }
    }
    void configureLocked(Config c) {
        auto s=viewLocked();
        // Grid edits recalibrate in either mode; keep an explicit replacement Re in manual mode.
        if ((c.n!=s.config.n||c.k0!=s.config.k0)&&(s.regulated||c.reynolds==s.config.reynolds)) c.reynolds=initialReynolds(c.n,c.k0);
        if(c==s.config)return;
        options.config=c;
        if (controller) controller->configure(c);
    }
    void runningLocked(bool value) {options.paused=!value;if(controller)controller->running(value);}
    void resetLocked() {if(controller)controller->reset();}
    Json report() {
        std::lock_guard lock(mutex);
        auto s=viewLocked();auto d=options.display;
        double re=s.stats.reynolds>0?s.stats.reynolds:s.config.reynolds;
        Json j={{"config",configReport(s.config)},{"ready",s.ready},{"running",s.running},{"settled",s.settled},
            {"generation",s.generation},{"gpu",gpuName},{"backend","CUDA / VkFFT"},
            {"error_message",!error.empty()?Json(error):!s.error.empty()?Json(s.error):Json(nullptr)},
            {"regulated",s.regulated},{"effective_reynolds",re},{"palinstrophy_control",10000*s.stats.metric},
            {"display",fieldNames[d.field]},{"palette",paletteNames[d.palette]},{"color_range",rangeNames[d.colorRange]},
            {"flow",d.flow},{"exposure",exposure},{"sigma",sigma},{"focused",focused},{"exporting",exporting},
            {"elapsed_seconds",s.wall},{"presentations",presentations},{"allocated_bytes",s.stats.allocatedBytes},
            {"history",s.history},{"performance",{{"simtime_per_s",s.rate},{"turnovers_per_s",s.turnoversRate},
                {"steps_per_s",s.stepsRate},{"milliseconds_per_step",s.stepsRate>0?1000/s.stepsRate:0},{"batch_steps",s.batchSteps}}}};
        if (s.ready) j["stats"]=statsReport(s.stats);
        if (s.frame) j["frame"]=frameReport(*s.frame);
        if (parent) {
            RECT r{};GetClientRect(parent,&r);double scale=GetDpiForWindow(parent)/96.;
            j["canvas"]={{"width_points",r.right/scale},{"height_points",r.bottom/scale},{"scale",scale},
                {"visible",visible&&bool(IsWindowVisible(parent))&&!IsIconic(GetAncestor(parent,GA_ROOT))}};
        }
        return j;
    }
    static LRESULT CALLBACK windowProc(HWND h,UINT msg,WPARAM w,LPARAM l) {
        auto self=reinterpret_cast<UIHandle*>(GetWindowLongPtrW(h,GWLP_USERDATA));
        if(msg==WM_NCCREATE){self=static_cast<UIHandle*>(reinterpret_cast<CREATESTRUCTW*>(l)->lpCreateParams);SetWindowLongPtrW(h,GWLP_USERDATA,reinterpret_cast<LONG_PTR>(self));}
        if(msg==WM_ERASEBKGND)return 1;
        if(msg==WM_PAINT){PAINTSTRUCT p;BeginPaint(h,&p);EndPaint(h,&p);return 0;}
        if(self&&msg==WM_LBUTTONUP) {
            try {
                std::lock_guard lock(self->mutex);RECT r{};GetClientRect(h,&r);float scale=GetDpiForWindow(h)/96.f;
                float x=GET_X_LPARAM(l)/scale,y=GET_Y_LPARAM(l)/scale;
                if(x>=14&&x<128&&y>=r.bottom/scale-49&&y<=r.bottom/scale-14&&self->controller&&self->controller->view().ready){
                    int action=int((x-14)/38);
                    if(action==0)self->runningLocked(!self->controller->view().running);
                    if(action==1){self->options.paused=true;self->controller->singleStep();}
                    if(action==2)self->resetLocked();
                }
            }catch(const std::exception& e){std::lock_guard lock(self->mutex);self->error=e.what();}
            SetFocus(GetParent(h));return 0;
        }
        if(self&&(msg==WM_KEYDOWN||msg==WM_KEYUP||msg==WM_CHAR))return SendMessageW(GetParent(h),msg,w,l);
        return DefWindowProcW(h,msg,w,l);
    }
    void attach(HWND host) {
        if (parent) throw std::runtime_error("Canvas is already attached.");
        if (!IsWindow(host)) throw std::runtime_error("Invalid parent HWND.");
        WNDCLASSW wc{};wc.lpfnWndProc=windowProc;wc.hInstance=GetModuleHandleW(nullptr);wc.lpszClassName=L"FastTurboPythonCanvas";wc.hCursor=LoadCursorW(nullptr,IDC_ARROW);
        if(!RegisterClassW(&wc)&&GetLastError()!=ERROR_CLASS_ALREADY_EXISTS)throw std::runtime_error("Cannot register canvas window.");
        RECT r{};GetClientRect(host,&r);parent=host;
        child=CreateWindowExW(WS_EX_NOPARENTNOTIFY,wc.lpszClassName,L"",WS_CHILD|WS_VISIBLE|WS_CLIPSIBLINGS,
            0,0,r.right,r.bottom,host,nullptr,wc.hInstance,this);
        if(!child){parent=nullptr;throw std::runtime_error("Cannot create native canvas.");}
        renderThread=std::thread([this]{renderLoop();});
    }
    void renderLoop() noexcept {
        try {
            // Match the reference app's 60 Hz deadline and Windows timer precision.
            struct TimerResolution {
                bool active=timeBeginPeriod(1)==TIMERR_NOERROR;
                ~TimerResolution(){if(active)timeEndPeriod(1);}
            } timerResolution;
            COMScope com;CUDA_CHECK(cudaSetDevice(deviceId));Renderer renderer(child);
            if(renderer.gpu()!=deviceId)throw std::runtime_error("CUDA and Direct3D selected different devices.");
            int previousW=0,previousH=0;
            uint64_t seenGeneration=0;
            double nextPresent=0;
            while(true) {
                ViewState s;Display display;bool draw;
                std::vector<std::function<void(Renderer&)>> pending;
                {
                    std::unique_lock lock(mutex);
                    if(stop)return;
                    s=viewLocked();display=options.display;draw=visible;pending.swap(jobs);
                    if(s.generation!=seenGeneration){seenGeneration=s.generation;presentations=0;}
                }
                for(auto& job:pending)job(renderer);
                RECT rect{};GetClientRect(parent,&rect);
                if(draw&&IsWindowVisible(parent)&&!IsIconic(GetAncestor(parent,GA_ROOT))&&rect.right>0&&rect.bottom>0&&nowSeconds()>=nextPresent) {
                    if(rect.right!=previousW||rect.bottom!=previousH) {
                        SetWindowPos(child,nullptr,0,0,rect.right,rect.bottom,SWP_NOZORDER|SWP_NOACTIVATE|SWP_ASYNCWINDOWPOS);
                        renderer.resize(rect.right,rect.bottom);previousW=rect.right;previousH=rect.bottom;
                    }
                    float scale=GetDpiForWindow(parent)/96.f,side=std::min(rect.right,rect.bottom)/scale;
                    renderer.begin(scale,true);renderer.end();
                    if(s.frame)renderer.drawField(s.frame,display,0,0,side,scale);
                    renderer.begin(scale,false);
                    auto white=D2D1::ColorF(.9f,.92f,.95f),dark=D2D1::ColorF(0,0,0,.35f);
                    if(!s.ready)renderer.text(L"Preparing the flow",D2D1::RectF(side/2-65,side/2-15,side/2+100,side/2+15),12,white);
                    renderer.rectangle(D2D1::RectF(14,14,204,40),dark,13);
                    renderer.text(L"UNFORCED · SEED "+std::to_wstring(s.config.seed),D2D1::RectF(24,14,204,40),8,white,true);
                    renderer.rectangle(D2D1::RectF(14,side-49,128,side-14),dark,18);
                    const wchar_t* icons[]={s.running?L"Ⅱ":L"▶",L"▸Ⅰ",L"↶"};
                    for(int i=0;i<3;++i)renderer.text(icons[i],D2D1::RectF(26+i*38.f,side-49,52+i*38.f,side-14),14,white);
                    std::wostringstream clock;clock.precision(3);clock<<L"t  "<<std::fixed<<s.stats.time;
                    renderer.rectangle(D2D1::RectF(side-114,side-46,side-14,side-17),dark,15);
                    renderer.text(clock.str(),D2D1::RectF(side-104,side-46,side-24,side-17),10,white,true);
                    renderer.end();renderer.present();
                    double completed=nowSeconds();
                    if(nextPresent==0)nextPresent=completed;
                    nextPresent+=(std::floor(std::max(0.,completed-nextPresent)*60)+1)/60;
                    {std::lock_guard lock(mutex);if(s.frame)++presentations;}
                    WaitForSingleObject(renderer.frameSignal(),16);
                } else {
                    double delay=nextPresent>nowSeconds()?std::min(.016,nextPresent-nowSeconds()):.016;
                    std::unique_lock lock(mutex);wake.wait_for(lock,std::chrono::duration<double>(delay),[&]{return stop||!jobs.empty();});
                }
            }
        }catch(const std::exception& e){
            std::lock_guard lock(mutex);error=e.what();stop=true;jobs.clear();wake.notify_all();
        }
    }
    void exportFrame(const Json& j) {
        std::shared_ptr<Frame> frame;Display display;
        auto promise=std::make_shared<std::promise<void>>();auto future=promise->get_future();
        {
            std::lock_guard lock(mutex);frame=viewLocked().frame;display=options.display;
            if(!frame)throw std::runtime_error("No completed frame.");
            if(stop||!child)throw std::runtime_error(error.empty()?"No native renderer.":error);
            jobs.push_back([frame,display,j,promise](Renderer& renderer){
                try{exportFields(*frame,j);if(has(j,"png"))renderer.exportPNG(frame,display,path(j,"png"),pixelSize(j));promise->set_value();}
                catch(...){promise->set_exception(std::current_exception());}
            });wake.notify_one();
        }
        // The owning render thread handles D3D and COM. ctypes releases the GIL.
        while(future.wait_for(std::chrono::milliseconds(50))!=std::future_status::ready){
            std::lock_guard lock(mutex);if(stop)throw std::runtime_error(error.empty()?"Renderer stopped.":error);
        }
        future.get();
    }
    ~UIHandle() {
        {std::lock_guard lock(mutex);stop=true;wake.notify_one();}
        if(renderThread.joinable())renderThread.join();
        controller.reset();
        if(child)DestroyWindow(child);
    }
};
}

// Small file/clock helpers adapted from reference-windows/src/main.cpp.
namespace turbo {
double nowSeconds(){return std::chrono::duration<double>(std::chrono::steady_clock::now().time_since_epoch()).count();}
std::vector<float> loadInitial(const Options& o) {
    if(o.initial.empty())return {};
    size_t count=size_t(o.config.n)*o.config.n;
    if(std::filesystem::file_size(o.initial)!=count*4)throw std::runtime_error("Initial file must contain exactly N² Float32 samples.");
    std::vector<float> data(count);std::ifstream in(o.initial,std::ios::binary);
    if(!in.read(reinterpret_cast<char*>(data.data()),std::streamsize(count*4)))throw std::runtime_error("Cannot read initial file.");
    return data;
}
void writeSpectrum(Solver& s,const Options& o) {
    if(!o.spectrum.empty())writeFile(o.spectrum,s.spectrum());
    if(!o.fields.empty()){auto f=s.snapshot(true);if(!f)throw std::runtime_error("No snapshot slot.");writeFile(o.fields,s.readFrame(*f));}
}
}

extern "C" {
void ft_string_free(char* pointer){std::free(pointer);}
char* ft_initialize(const char*) {
    return response([]{
        if(mainThread&&mainThread!=GetCurrentThreadId())throw std::runtime_error("Initialize on the main thread.");
        mainThread=GetCurrentThreadId();
        ComPtr<IDXGIFactory2> factory;hrCheck(CreateDXGIFactory1(IID_PPV_ARGS(&factory)),"Create DXGI factory");
        ComPtr<IDXGIAdapter1> adapter;bool found=false;
        for(UINT i=0;factory->EnumAdapters1(i,adapter.ReleaseAndGetAddressOf())!=DXGI_ERROR_NOT_FOUND;++i){
            if(cudaD3D11GetDevice(&deviceId,adapter.Get())==cudaSuccess){found=true;break;}
            cudaGetLastError();
        }
        if(!found)throw std::runtime_error("No CUDA-compatible Direct3D adapter found.");
        CUDA_CHECK(cudaSetDevice(deviceId));
        cudaDeviceProp prop{};CUDA_CHECK(cudaGetDeviceProperties(&prop,deviceId));gpuName=prop.name;
        MEMORYSTATUSEX memory{};memory.dwLength=sizeof(memory);GlobalMemoryStatusEx(&memory);
        Json info{{"abi_version",1},{"defaults",configReport(Config{})},{"backend","CUDA / VkFFT"},
            {"gpu",gpuName},{"os","Windows"},{"physical_memory_bytes",memory.ullTotalPhys},
            {"gpu_memory_bytes",prop.totalGlobalMem},
            {"capabilities",{{"native_rendering",true},{"forcing",false},{"drag",false},{"vortex_injection",false}}}};
        int version=0;
        if(cudaRuntimeGetVersion(&version)==cudaSuccess)info["cuda_runtime_version"]=version;
        version=0;
        if(cudaDriverGetVersion(&version)==cudaSuccess)info["cuda_driver_version"]=version;
        return info;
    });
}
void* ft_engine_create(const char* input,char** error) {
    try{CUDA_CHECK(cudaSetDevice(deviceId));return new EngineHandle(decode(input));}
    catch(const std::exception& e){if(error)*error=copyString(e.what());return nullptr;}
}
void ft_engine_destroy(void* handle){if(handle){cudaSetDevice(deviceId);delete static_cast<EngineHandle*>(handle);}}
char* ft_engine_call(void* handle,const char* input) {
    return response([&]()->Json{
        if(!handle)throw std::runtime_error("Closed engine.");
        auto& h=*static_cast<EngineHandle*>(handle);std::lock_guard lock(h.mutex);CUDA_CHECK(cudaSetDevice(deviceId));
        auto j=decode(input);auto op=j.value("op","");auto& core=h.core;
        if(op=="inspect")return statsReport(core.inspect());
        if(op=="advance")return statsReport(core.advance(j.value("steps",1),has(j,"until")?std::optional<double>(scalar(j,"until",0)):std::nullopt));
        if(op=="set"){
            if(has(j,"reynolds"))core.setReynolds(scalar(j,"reynolds",0));
            if(has(j,"cfl"))core.setCFL(float(scalar(j,"cfl",0)));
            return Json::object();
        }
        if(op=="reset"){Options o;o.config=core.configuration();o.initial=path(j,"initial");core.reset(loadInitial(o));return statsReport(core.inspect());}
        if(op=="export"){
            if(has(j,"spectrum"))writeFile(path(j,"spectrum"),core.spectrum());
            if(j.value("snapshot",false)||has(j,"fields")||has(j,"png")){
                auto f=core.snapshot(j.value("include_velocity",true));if(!f)throw std::runtime_error("No snapshot slot.");
                exportFields(*f,j);
                if(has(j,"png")){
                    COMScope com;
                    HWND window=CreateWindowExW(0,L"STATIC",L"FastTurbo export",WS_POPUP,0,0,64,64,nullptr,nullptr,GetModuleHandleW(nullptr),nullptr);
                    if(!window)throw std::runtime_error("Cannot create export surface.");
                    try{{Renderer renderer(window);renderer.exportPNG(f,displayOptions(j),path(j,"png"),pixelSize(j));}DestroyWindow(window);}
                    catch(...){DestroyWindow(window);throw;}
                }
                return frameReport(*f);
            }
            return Json::object();
        }
        throw std::invalid_argument("Unknown engine operation.");
    });
}
void* ft_ui_create(){try{mainOnly();return new UIHandle;}catch(...){return nullptr;}}
void ft_ui_destroy(void* handle){if(handle&&GetCurrentThreadId()==mainThread)delete static_cast<UIHandle*>(handle);}
char* ft_ui_attach(void* handle,void* parent){return response([&]{mainOnly();if(!handle)throw std::runtime_error("Closed UI.");static_cast<UIHandle*>(handle)->attach(static_cast<HWND>(parent));return Json::object();});}
char* ft_ui_call(void* handle,const char* input) {
    return response([&]()->Json{
        mainOnly();if(!handle)throw std::runtime_error("Closed UI.");auto& h=*static_cast<UIHandle*>(handle);
        auto j=decode(input);auto op=j.value("op","");
        if(op=="export")h.exportFrame(j);
        else if(op=="save"){
            wchar_t name[32768]=L"2DFastTurbo.png";OPENFILENAMEW dialog{};dialog.lStructSize=sizeof(dialog);
            dialog.hwndOwner=h.parent?GetAncestor(h.parent,GA_ROOT):nullptr;dialog.lpstrFile=name;dialog.nMaxFile=32768;
            dialog.lpstrFilter=L"PNG image\0*.png\0\0";dialog.lpstrDefExt=L"png";dialog.Flags=OFN_OVERWRITEPROMPT|OFN_PATHMUSTEXIST|OFN_NOCHANGEDIR;
            if(GetSaveFileNameW(&dialog)){auto bytes=std::filesystem::path(name).u8string();h.exportFrame({{"png",std::string(bytes.begin(),bytes.end())}});}
            else if(CommDlgExtendedError())throw std::runtime_error("Could not open Save PNG dialog.");
        } else {
            std::lock_guard lock(h.mutex);auto s=h.viewLocked();
            if(op=="get"||op=="flush"){}
            else if(op=="start")h.startLocked();
            else if(op=="toggle")h.runningLocked(!s.running);
            else if(op=="running")h.runningLocked(j.value("value",false));
            else if(op=="step"){h.options.paused=true;if(h.controller)h.controller->singleStep();}
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
