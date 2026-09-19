#include "options.hpp"
#include "resources.hpp"
#include "cuda_support.cuh"
#include <chrono>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <map>
#include <set>
#include <sstream>
#include <string_view>
#include <windows.h>
#include <shellapi.h>

namespace turbo {
double nowSeconds(){return std::chrono::duration<double>(std::chrono::steady_clock::now().time_since_epoch()).count();}
static std::string quote(const std::string& s) {
    std::string r="\"";for(unsigned char c:s) {if(c=='"'||c=='\\')r+='\\'; if(c>=32)r+=char(c);else r+=' ';}return r+'"';
}
std::vector<float> loadInitial(const Options& o) {
    if(o.initial.empty())return {};
    size_t count=size_t(o.config.n)*o.config.n;
    if(std::filesystem::file_size(o.initial)!=count*4)throw std::runtime_error("Initial file must contain exactly N² Float32 samples.");
    std::vector<float> data(count);std::ifstream in(o.initial,std::ios::binary);
    if(!in.read(reinterpret_cast<char*>(data.data()),std::streamsize(count*4)))throw std::runtime_error("Cannot read initial file.");
    return data;
}
void writeSpectrum(Solver& s,const Options& o) {
    if(!o.spectrum.empty()) {
        auto values=s.spectrum();std::ofstream out(o.spectrum,std::ios::binary);
        if(!out.write(reinterpret_cast<char*>(values.data()),std::streamsize(values.size()*sizeof(Complex))))throw std::runtime_error("Cannot write spectrum.");
    }
    if(!o.fields.empty()) {
        auto f=s.snapshot(true);if(!f)throw std::runtime_error("No frame slot for export.");
        auto values=s.readFrame(*f);std::ofstream out(o.fields,std::ios::binary);
        if(!out.write(reinterpret_cast<char*>(values.data()),std::streamsize(values.size()*4)))throw std::runtime_error("Cannot write fields.");
    }
}
std::string reportJSON(const Options& o,const Statistics& initial,const Statistics& final,double seconds,
                       double gpuSeconds,int batches,int snapshots,const char* mode,double fps) {
    cudaDeviceProp prop{};int device=0;CUDA_CHECK(cudaGetDevice(&device));CUDA_CHECK(cudaGetDeviceProperties(&prop,device));
    std::ostringstream out;out<<std::setprecision(17);
    auto stats=[&](const Statistics& s){out<<"{\"steps\":"<<s.step<<",\"simulation_time\":"<<s.time<<",\"dt\":"<<s.dt
        <<",\"energy\":"<<s.energy<<",\"enstrophy\":"<<s.enstrophy<<",\"palinstrophy\":"<<s.palinstrophy
        <<",\"palinstrophy_metric\":"<<s.metric<<",\"reynolds\":"<<s.reynolds<<",\"initial_turnover_time\":"<<s.turnover<<",\"is_finite\":true}";};
    double elapsed=std::max(seconds,1e-12),advance=final.time-initial.time;
    out<<"{\n\"schema_version\":1,\"solver\":\"2DFastTurbo Windows CUDA/VkFFT\",\"mode\":"<<quote(mode)
       <<",\n\"config\":{\"resolution\":"<<o.config.n<<",\"padded_resolution\":"<<o.config.m()
       <<",\"reynolds\":"<<o.config.reynolds<<",\"viscosity\":"<<float(1/o.config.reynolds)
       <<",\"k0\":"<<o.config.k0<<",\"seed\":"<<o.config.seed<<",\"cfl\":"<<o.config.cfl
       <<",\"integrator\":"<<quote(methods[int(o.config.integrator)])<<",\"operator\":"<<quote(forms[int(o.config.form)])
       <<",\"start_spectrum\":"<<quote(startSpectra[int(o.config.startSpectrum)])
       <<",\"batch\":"<<o.batch<<",\"fixed_dt\":"<<(o.config.fixedDt?std::to_string(o.config.fixedDt):"null")<<"},\n\"initial_stats\":";
    stats(initial);out<<",\n\"stats\":";stats(final);
    out<<",\n\"seconds\":"<<seconds<<",\"simtime_per_s\":"<<advance/elapsed<<",\"steps_per_s\":"<<double(final.step-initial.step)/elapsed
       <<",\"turnovers_per_s\":"<<(initial.turnover>0?advance/(elapsed*initial.turnover):0)
       <<",\"gpu_seconds\":"<<gpuSeconds<<",\"batches\":"<<batches<<",\"snapshots\":"<<snapshots<<",\"presented_fps\":"<<fps
       <<",\"warmup_steps\":"<<o.warmup<<",\"spectrum_normalization\":\"fourier-series\""
       <<",\"vorticity_convention\":\"omega = dx(v) - dy(u)\",\"initial_source\":"<<quote(o.initial.empty()?(o.config.startSpectrum==StartSpectrum::km3?"seeded-km3":"seeded-pao"):"external-physical-field")
       <<",\"hardware\":{\"gpu\":"<<quote(prop.name)<<",\"owned_solver_buffer_bytes\":"<<final.allocatedBytes<<",\"vkfft_internal_bytes\":"<<final.fftBytes<<"}\n}\n";
    return out.str();
}
void writeReport(const Options& o,const std::string& json) {
    if(!o.output.empty()) {std::ofstream out(o.output);if(!(out<<json))throw std::runtime_error("Cannot write result JSON.");}
    std::cout<<json<<std::flush;
}
static Options parse(int argc,char** argv) {
    Options o;std::map<std::string,std::string> values;
    const std::set<std::string> keys={"--resolution","--reynolds","--k0","--seed","--cfl","--integrator","--operator","--start-spectrum","--fixed-dt","--steps","--until","--turnovers","--wall-seconds","--batch","--warmup","--initial","--spectrum","--fields","--output","--snapshot-hz","--display","--palette","--color-range","--sigma","--exposure","--export","--capture","--render-check","--canvas-size","--gui-warmup-seconds"};
    for(int i=1;i<argc;++i) {
        std::string key=argv[i];
        if(key=="--help"){o.help=true;continue;}
        if(key=="--licenses"){o.licenses=true;continue;}
        if(key=="--benchmark"){o.benchmark=true;continue;}
        if(key=="--gui-benchmark"){o.guiBenchmark=true;continue;}
        if(key=="--live-check"){o.liveCheck=true;continue;}
        if(key=="--paused"){o.paused=true;continue;}
        if(key=="--flow-lines"){o.display.flow=true;continue;}
        if(!keys.contains(key)||i+1==argc||values.contains(key))throw std::invalid_argument("Unknown, incomplete or repeated option: "+key);
        values[key]=argv[++i];
    }
    auto number=[&](const char* key,double fallback) {if(!values.contains(key))return fallback;size_t used=0;double v=std::stod(values.at(key),&used);if(used!=values.at(key).size()||!std::isfinite(v))throw std::invalid_argument(std::string(key)+" must be finite.");return v;};
    auto integer=[&](const char* key,int fallback) {double v=number(key,fallback);if(v!=std::trunc(v)||v<0||v>std::numeric_limits<int>::max())throw std::invalid_argument(std::string(key)+" must be a nonnegative integer.");return int(v);};
    auto choice=[&](const char* key,std::initializer_list<const char*> names,int fallback) {if(!values.contains(key))return fallback;int i=0;for(auto name:names){if(values.at(key)==name)return i;++i;}throw std::invalid_argument(std::string("Invalid ")+key);};
    bool interactive=!o.benchmark&&!o.guiBenchmark;
    o.config.n=integer("--resolution",interactive?2048:o.config.n);o.config.k0=integer("--k0",interactive?10:o.config.k0);
    o.config.reynolds=number("--reynolds",interactive?initialReynolds(o.config.n,o.config.k0):o.config.reynolds);
    o.regulated=!interactive&&!values.contains("--reynolds");
    o.config.cfl=float(number("--cfl",o.config.cfl));o.config.fixedDt=float(number("--fixed-dt",0));
    if(values.contains("--fixed-dt")&&!(o.config.fixedDt>0))throw std::invalid_argument("Fixed dt must be positive.");
    if(values.contains("--seed")) {auto v=values.at("--seed");if(v.empty()||v.find_first_not_of("0123456789")!=std::string::npos)throw std::invalid_argument("Seed must be uint64.");o.config.seed=std::stoull(v);}
    o.config.integrator=Integrator(choice("--integrator",{"imexRK3","ifHeunRK3","etdrk4"},2));
    o.config.form=Form(choice("--operator",{"stress","advective"},0));
    o.config.startSpectrum=StartSpectrum(choice("--start-spectrum",{"PAO","KM3"},0));
    o.steps=integer("--steps",200);o.batch=integer("--batch",8);o.fixedBatch=values.contains("--batch");o.warmup=integer("--warmup",8);
    o.wallSeconds=number("--wall-seconds",0);o.snapshotHz=number("--snapshot-hz",0);
    o.guiWarmup=number("--gui-warmup-seconds",2);o.canvasSide=number("--canvas-size",640);
    if(values.contains("--until"))o.until=number("--until",0);
    if(values.contains("--turnovers"))o.turnovers=number("--turnovers",0);
    int endpoints=0;for(auto key:{"--steps","--until","--turnovers","--wall-seconds"})endpoints+=values.contains(key);
    if(endpoints>1||o.batch<1||o.batch>32||o.warmup>4096||o.wallSeconds<0||o.snapshotHz<0||o.snapshotHz>240||o.guiWarmup<0||o.canvasSide<576||o.canvasSide>4096||(o.until&&*o.until<0)||(o.turnovers&&*o.turnovers<0))throw std::invalid_argument("Invalid benchmark interval, batch or canvas size.");
    if(values.contains("--wall-seconds")&&o.wallSeconds<=0)throw std::invalid_argument("Wall seconds must be positive.");
    auto path=[&](const char* key){if(!values.contains(key))return std::filesystem::path{};const auto& s=values.at(key);return std::filesystem::path(std::u8string(reinterpret_cast<const char8_t*>(s.data()),s.size()));};
    o.initial=path("--initial");o.spectrum=path("--spectrum");o.fields=path("--fields");o.output=path("--output");o.exportImage=path("--export");o.capture=path("--capture");
    o.renderCheck=path("--render-check");
    o.display.field=choice("--display",{"vorticity","speed","enstrophy"},0);
    o.display.palette=choice("--palette",{"aurora","ember","glacier","inferno"},1);
    o.display.colorRange=choice("--color-range",{"sigma","exposure"},0);
    o.display.exposure=float(number("--exposure",1.2));o.display.sigma=float(number("--sigma",2.5));
    if(o.display.exposure<.25||o.display.exposure>3||o.display.sigma<1.5||o.display.sigma>3.5)throw std::invalid_argument("Contrast is outside the reference range.");
    if(o.benchmark&&(o.guiBenchmark||o.liveCheck))throw std::invalid_argument("Choose headless or GUI operation.");
    if(o.guiBenchmark&&endpoints==0)o.until=10;
    o.config.validate();return o;
}
static int benchmark(const Options& o) {
    auto field=loadInitial(o);Solver core(o.config,field);
    if(o.warmup) {core.advance(o.warmup);if(o.snapshotHz>0||!o.fields.empty())core.snapshot();core.reset(field);}
    auto initial=core.inspect(),stats=initial;auto endpoint=o.until;
    if(o.turnovers)endpoint=*o.turnovers*initial.turnover;
    int batches=0,snapshots=0;double gpu=0,next=0,start=nowSeconds();
    while(o.wallSeconds>0?nowSeconds()-start<o.wallSeconds:endpoint?!reached(stats.time,*endpoint):stats.step<uint64_t(o.steps)) {
        int count=(!endpoint&&o.wallSeconds==0)?std::min(o.batch,o.steps-int(stats.step)):o.batch;
        auto before=stats;stats=core.advance(count,endpoint);++batches;gpu+=stats.gpuMilliseconds/1000;
        if(stats.step<=before.step||stats.time<=before.time)throw std::runtime_error("Solver stopped making physical progress.");
        double elapsed=nowSeconds()-start;
        if(o.snapshotHz>0&&elapsed>=next){core.snapshot();++snapshots;next=elapsed+1/o.snapshotHz;}
    }
    stats=core.inspect();double seconds=nowSeconds()-start;
    writeSpectrum(core,o);writeReport(o,reportJSON(o,initial,stats,seconds,gpu,batches,snapshots,o.snapshotHz>0?"headless-with-snapshots":"headless"));return 0;
}
}
static int run(int argc,char** argv) {
    try {
        auto o=turbo::parse(argc,argv);
        if(o.licenses){for(int id:{201,202}){auto text=turbo::resource(id);std::cout.write(text.data(),text.size());std::cout<<'\n';}return 0;}
        if(o.help) {
            std::cout<<"2DFastTurbo Windows: native CUDA / VkFFT / D3D11\n"
              "No options: launch GUI. Space pause; Right single step; R reset; F focus; Ctrl+S save; Ctrl+Shift+R next seed.\n"
              "--benchmark | --gui-benchmark | --live-check | --licenses\n"
              "--resolution N --k0 K --seed S --reynolds R --cfl C\n"
              "--integrator imexRK3|ifHeunRK3|etdrk4 --operator stress|advective\n"
              "--steps K | --until T | --turnovers K | --wall-seconds S\n"
              "--batch 1..32 --warmup K --fixed-dt DT --snapshot-hz HZ\n"
              "--start-spectrum PAO|KM3 (default PAO)\n"
              "--initial FILE --spectrum FILE --fields FILE --output FILE\n"
              "--paused --display vorticity|speed|enstrophy --palette aurora|ember|glacier|inferno\n"
              "--color-range sigma|exposure --sigma 1.5..3.5 --exposure .25..3 --flow-lines\n"
              "--export PNG --capture PNG --render-check DIRECTORY --canvas-size SIDE --gui-warmup-seconds S\n"
              "GUI defaults: retained N2048, M3072, k0=10, calibrated Re, regulation off.\n"
              "Benchmark defaults: retained N1024, M1536, k0=4, Re=27898.36614974335.\n"
              "Common defaults: seed=2, CFL=3, ETD RK4, Stress.\n";return 0;
        }
        return o.benchmark?turbo::benchmark(o):turbo::runApp(o);
    }catch(const std::exception& e){
        std::cerr<<"2DFastTurbo: "<<e.what()<<'\n';
        HANDLE error=GetStdHandle(STD_ERROR_HANDLE);
        if(!error||error==INVALID_HANDLE_VALUE)MessageBoxA(nullptr,e.what(),"2DFastTurbo",MB_OK|MB_ICONERROR);
        return 2;
    }
}
int WINAPI wWinMain(HINSTANCE,HINSTANCE,PWSTR,int) {
    int argc=0;LPWSTR* wide=CommandLineToArgvW(GetCommandLineW(),&argc);
    if(!wide)return 2;
    std::vector<std::string> args;args.reserve(argc);
    for(int i=0;i<argc;++i){
        int size=WideCharToMultiByte(CP_UTF8,0,wide[i],-1,nullptr,0,nullptr,nullptr);
        std::string value(size,'\0');WideCharToMultiByte(CP_UTF8,0,wide[i],-1,value.data(),size,nullptr,nullptr);value.pop_back();args.push_back(std::move(value));
    }
    LocalFree(wide);std::vector<char*> argv;for(auto& arg:args)argv.push_back(arg.data());
    return run(argc,argv.data());
}
