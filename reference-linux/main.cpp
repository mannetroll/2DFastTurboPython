#include "reference.hpp"
#include "cuda_loader.hpp"
#include "cuda_support.cuh"
#include <charconv>
#include <chrono>
#include <ctime>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <map>
#include <numbers>
#include <set>
#include <sys/utsname.h>

namespace {
using Clock=std::chrono::steady_clock;
double elapsed(Clock::time_point start){return std::chrono::duration<double>(Clock::now()-start).count();}
struct Options {
    turbo::Config config;
    turbo::legacy::Config legacy;
    int steps=1000,warmup=100,device=0,batch=8;
    bool help=false,benchmark=false,validation=false,json=false,positional=false;
    std::filesystem::path root=FASTTURBO_REFERENCE_ROOT,output,spectrum,fields,initial,compare,dump;
};
uint64_t integer(const std::string& text,const std::string& name,uint64_t limit=2147483647) {
    uint64_t result=0;auto [end,error]=std::from_chars(text.data(),text.data()+text.size(),result);
    if(error!=std::errc{}||end!=text.data()+text.size()||text.empty()||result>limit)
        throw std::invalid_argument(name+" requires an unsigned integer in range.");
    return result;
}
double real(const std::string& text,const std::string& name) {
    size_t used=0;double value=std::stod(text,&used);
    if(used!=text.size()||!std::isfinite(value)||!(value>0))throw std::invalid_argument(name+" requires a finite positive number.");
    return value;
}
Options parse(int argc,char** argv) {
    Options o;std::map<std::string,std::string> values;std::set<std::string> flags;
    int start=1;
    if(argc>1&&argv[1][0]!='-') {
        if(argc<10)throw std::invalid_argument("Expected N Re K0 Steps CFL cuda Update PAO|KM3 CNAB2.");
        o.positional=true;auto& c=o.legacy;
        c.n=int(integer(argv[1],"N"));c.reynolds=real(argv[2],"Re");c.k0=real(argv[3],"K0");
        c.steps=int(integer(argv[4],"Steps"));c.cfl=real(argv[5],"CFL");c.update=int(integer(argv[7],"Update"));
        const std::string spectrum=argv[8];
        if(std::string(argv[6])!="cuda"||(spectrum!="PAO"&&spectrum!="KM3")||std::string(argv[9])!="CNAB2")
            throw std::invalid_argument("Legacy mode requires backend cuda, spectrum PAO or KM3 and method CNAB2.");
        c.startSpectrum=spectrum=="KM3"?turbo::StartSpectrum::km3:turbo::StartSpectrum::pao;
        if(const char* seed=std::getenv("SCIPYTURBO_SEED"))c.seed=int(integer(seed,"SCIPYTURBO_SEED",5010));
        start=10;
    }
    const std::set<std::string> keys={"--n","--steps","--warmup","--seed","--device","--batch","--reynolds","--k0","--cfl","--integrator","--operator","--start-spectrum","--fixed-dt","--reference-root","--output","--spectrum","--fields","--initial","--compare-spectrum","--dump"};
    for(int i=start;i<argc;++i) {
        std::string key=argv[i];
        if(key=="--help"||key=="--benchmark"||key=="--validate"||key=="--json") {
            if(!flags.insert(key).second)throw std::invalid_argument("Repeated option: "+key);
        } else {
            if(!keys.contains(key)||i+1==argc||values.contains(key))throw std::invalid_argument("Unknown, incomplete or repeated option: "+key);
            values[key]=argv[++i];
        }
    }
    o.help=flags.contains("--help");o.json=flags.contains("--json");o.benchmark=flags.contains("--benchmark");o.validation=flags.contains("--validate");
    if(o.help)return o;
    if(o.positional) {
        if(o.benchmark||o.validation||o.json)throw std::invalid_argument("Legacy positional mode writes the historical text log; use --output for JSON.");
        for(const auto& [key,value]:values)
            if(key!="--device"&&key!="--output"&&key!="--dump")throw std::invalid_argument("Not a legacy option: "+key);
        o.legacy.validate();
    }
    auto number=[&](const char* key,int fallback){return values.contains(key)?int(integer(values.at(key),key)):fallback;};
    o.config.n=number("--n",o.config.n);o.steps=number("--steps",o.steps);o.warmup=number("--warmup",o.warmup);
    o.device=number("--device",0);o.batch=number("--batch",8);o.config.k0=number("--k0",o.config.k0);
    if(values.contains("--seed"))o.config.seed=integer(values.at("--seed"),"--seed",UINT64_MAX);
    if(values.contains("--reynolds"))o.config.reynolds=real(values.at("--reynolds"),"--reynolds");
    if(values.contains("--cfl"))o.config.cfl=float(real(values.at("--cfl"),"--cfl"));
    if(values.contains("--fixed-dt")) {
        o.config.fixedDt=float(real(values.at("--fixed-dt"),"--fixed-dt"));
        if(!(o.config.fixedDt>0))throw std::invalid_argument("--fixed-dt underflows float32.");
    }
    auto choice=[&](const char* key,const auto& choices,int fallback){
        if(!values.contains(key))return fallback;
        for(size_t i=0;i<std::size(choices);++i)if(values.at(key)==choices[i])return int(i);
        throw std::invalid_argument(std::string("Invalid ")+key);
    };
    o.config.integrator=turbo::Integrator(choice("--integrator",turbo::methods,2));
    o.config.form=turbo::Form(choice("--operator",turbo::forms,0));
    o.config.startSpectrum=turbo::StartSpectrum(choice("--start-spectrum",turbo::startSpectra,0));
    auto path=[&](const char* key,std::filesystem::path& target){if(values.contains(key))target=values.at(key);};
    path("--reference-root",o.root);path("--output",o.output);path("--spectrum",o.spectrum);path("--fields",o.fields);path("--initial",o.initial);path("--compare-spectrum",o.compare);path("--dump",o.dump);
    if(!o.positional&&!o.benchmark&&!o.validation)o.benchmark=true;
    if(o.batch<1||o.batch>256||(!o.positional&&o.benchmark&&o.steps<1))throw std::invalid_argument("Benchmark steps must be positive; batch must be 1..256.");
    if(!o.positional&&!o.dump.empty())throw std::invalid_argument("--dump is a legacy option.");
    o.config.validate();return o;
}
Json deviceInfo(int index) {
    initializeCudaLoader();int count=0;CUDA_CHECK(cudaGetDeviceCount(&count));
    if(!count)throw std::runtime_error("No CUDA device available; CUDA/VkFFT execution is required.");
    if(index>=count)throw std::invalid_argument("CUDA device index out of range (device count "+std::to_string(count)+").");
    CUDA_CHECK(cudaSetDevice(index));CUDA_CHECK(cudaFree(nullptr));
    cudaDeviceProp p{};CUDA_CHECK(cudaGetDeviceProperties(&p,index));int runtime=0,driver=0;
    CUDA_CHECK(cudaRuntimeGetVersion(&runtime));CUDA_CHECK(cudaDriverGetVersion(&driver));
    auto version=[](int value){return std::to_string(value/1000)+"."+std::to_string(value%1000/10);};
    utsname system{};uname(&system);
    std::string os=std::string(system.sysname)+" "+system.release,line;
    std::ifstream release("/etc/os-release");
    while(std::getline(release,line))if(line.starts_with("PRETTY_NAME=")) {
        auto name=line.substr(12);
        if(name.size()>=2&&name.front()=='"'&&name.back()=='"')name=name.substr(1,name.size()-2);
        os=name+" / "+system.release;
    }
    return {{"gpu",p.name},{"device",index},{"compute_capability",std::to_string(p.major)+"."+std::to_string(p.minor)},
        {"architecture","sm_"+std::to_string(p.major)+std::to_string(p.minor)},{"vram_bytes",p.totalGlobalMem},
        {"cuda_runtime",version(runtime)},{"cuda_driver",version(driver)},{"backend","CUDA + VkFFT"},
        {"vkfft_version",FASTTURBO_VKFFT_VERSION},{"precision","float32"},{"os",os},
        {"fft_configuration","backend=1; unnormalized R2C/C2R; 3/2 padding; LUT for M>=768; fmad=false"}};
}
void printDevice(const Json& hardware) {
    std::cout<<"FastTurbo native Linux reference\n";
    for(auto key:{"gpu","device","compute_capability","vram_bytes","cuda_runtime","cuda_driver","backend","vkfft_version","precision","os"})
        std::cout<<key<<": "<<hardware[key]<<'\n';
}
template<class T>void writeArray(const std::filesystem::path& path,const std::vector<T>& values) {
    std::ofstream out(path,std::ios::binary);
    if(!out.write(reinterpret_cast<const char*>(values.data()),std::streamsize(values.size()*sizeof(T))))throw std::runtime_error("Cannot write "+path.string());
}
void writeJSON(const std::filesystem::path& path,const Json& value) {
    if(path.empty())return;
    std::ofstream out(path);
    if(!(out<<value.dump(2)<<'\n'))throw std::runtime_error("Cannot write "+path.string());
}
Json legacyRun(const Options& o,const Json& hardware) {
    const auto& c=o.legacy;auto begin=Clock::now();turbo::legacy::Solver solver(c);
    auto initial=solver.initialRow();double seconds=elapsed(begin);const auto& init=solver.initial();
    std::cout<<"CUDA/VkFFT: "<<hardware["gpu"].get<std::string>()<<"\nOS: "<<hardware["os"].get<std::string>()<<"\n--- RUN DNS ---\n";
    const auto reynolds=Json(c.reynolds).dump(),k0=Json(c.k0).dump(),cfl=Json(c.cfl).dump();
    const char* spectrum=turbo::startSpectra[int(c.startSpectrum)];
    std::printf(" N   = %d\n Re  = %s\n K0  = %s\n Steps = %d\n CFL  = %s\n Update = %d\n Start spectrum = %s\n Method = CNAB2\n requested = cuda\n",c.n,reynolds.c_str(),k0.c_str(),c.steps,cfl.c_str(),c.update,spectrum);
    std::cout<<"FFT: VkFFT CUDA R2C on "<<hardware["gpu"].get<std::string>()<<'\n';
    auto now=std::time(nullptr);std::cout<<"--- INITIALIZING CUDA --- "<<std::put_time(std::localtime(&now),"%Y-%m-%d %H:%M")<<'\n';
    std::printf(" N=%d, K0=%s, Re=%s\n Start spec. = %s\n PAO initializer = native historical Fortran LCG (Python spectrum contract; no Python runtime)\n",c.n,k0.c_str(),reynolds.c_str(),
        c.startSpectrum==turbo::StartSpectrum::km3?"KM3 k^-3 shell redistribution (cudaturbo)":"PAO k*exp(-(k/K0)^2)");
    double visc=init.viscosity,ep=visc*init.wiwi,kol=std::pow(visc*visc*visc/ep,.25);
    float dx=2.f*std::numbers::pi_v<float>/float(c.n);
    std::printf(" N           = %d\n Reynolds n. = %.8g\n K0          = %.8g\n Energy      = %.8g\n WiWi        = %.8g\n Time scale  = %.8g\n Kolmogorov  = %.8g\n Viscosity   = %.8g\n dx/Kol.     = %.8g\n PAO seed in = %d\n PAO seed out= %d\n %s INITIALIZATION OK. VISC=%.8g\n",c.n,c.reynolds,c.k0,init.energy,init.wiwi,.5*init.energy/ep,kol,visc,double(dx)/kol,c.seed,init.seedOut,spectrum,visc);
    std::printf(" effective = cuda (native CUDA CNAB2 comparison runner)\n DNS INITIALIZATION took %.3f seconds\n CUDA allocated memory ≈ %.2f MiB\n",seconds,double(solver.allocatedBytes())/1048576);
    std::printf(" [NEXTDT INIT] CFLM=%11.4f DT=%11.7f CN=%11.7f\n Initial DT=%11.7f CN=%11.7f\n",initial.cflMaximum,initial.dt,initial.cn,initial.dt,initial.cn);
    Json rows=Json::array();begin=Clock::now();
    auto final=solver.advance([&](const auto& r) {
        std::printf(" ITERATION %6d T=%12.10f DT=%10.8f CN=%10.8f CFLM=%.6f TAU_L=%.6f\n",r.iteration,r.time,r.dt,r.cn,r.cflMaximum,r.tau);
        rows.push_back({{"iteration",r.iteration},{"T",r.time},{"state_time",r.stateTime},{"DT",r.dt},{"CN",r.cn},{"CFLM",r.cflMaximum},{"TAU_L",r.tau}});
    });
    double wall=elapsed(begin);
    std::printf(" Elapsed CPU time for %d steps (s) = %.8g\n Final T=%.8g  CN=%.8g  DT=%.8g\n FPS = %.8g\n",c.steps,wall,final.stateTime,final.cn,final.dt,c.steps/wall);
    if(!o.dump.empty()) {
        std::filesystem::create_directories(o.dump);
        writeArray(o.dump/"initial-omega.c64",init.omega);writeArray(o.dump/"initial-velocity.c64",init.velocity);
        writeArray(o.dump/"omega.c64",solver.spectrum());writeArray(o.dump/"history.c64",solver.history());writeArray(o.dump/"velocity.f32",solver.velocity());
    }
    return {{"schema_version",1},{"mode","legacy-cnab2"},{"hardware",hardware},
        {"config",{{"N",c.n},{"Re",c.reynolds},{"K0",c.k0},{"steps",c.steps},{"CFL",c.cfl},{"update",c.update},{"seed",c.seed},{"start_spectrum",spectrum}}},
        {"initial",{{"Energy",init.energy},{"WiWi",init.wiwi},{"viscosity",init.viscosity},{"seed_out",init.seedOut},{"DT",initial.dt},{"CN",initial.cn},{"CFLM",initial.cflMaximum}}},
        {"iterations",rows},{"final",{{"T",final.stateTime},{"DT",final.dt},{"CN",final.cn},{"steps",final.iteration}}},
        {"initialization_seconds",seconds},{"wall_seconds",wall},{"steps_per_second",c.steps/wall}};
}
Json benchmark(const Options& o) {
    auto start=Clock::now();std::vector<float> field;
    if(!o.initial.empty()) {
        size_t count=size_t(o.config.n)*o.config.n;
        if(std::filesystem::file_size(o.initial)!=count*4)throw std::invalid_argument("--initial must contain N*N float32 values.");
        field.resize(count);std::ifstream in(o.initial,std::ios::binary);
        if(!in.read(reinterpret_cast<char*>(field.data()),std::streamsize(count*4)))throw std::runtime_error("Cannot read initial field.");
    }
    turbo::Solver solver(o.config,field);auto initial=solver.inspect();double initialization=elapsed(start);
    if(o.warmup)solver.advance(o.warmup);
    auto before=solver.inspect();CUDA_CHECK(cudaDeviceSynchronize());
    double gpuMilliseconds=0;start=Clock::now();
    for(int remaining=o.steps;remaining>0;) {
        int count=std::min(o.batch,remaining);auto s=solver.advance(count);gpuMilliseconds+=s.gpuMilliseconds;remaining-=count;
    }
    CUDA_CHECK(cudaDeviceSynchronize());double wall=elapsed(start);
    auto final=solver.inspect();
    if(final.step-before.step!=uint64_t(o.steps)||!(final.time>before.time)||!(wall>0))throw std::runtime_error("CUDA solver did not complete the requested steps.");
    Json parity=nullptr;
    if(!o.spectrum.empty()||!o.compare.empty()) {
        auto state=solver.spectrum();if(!o.spectrum.empty())writeArray(o.spectrum,state);
        if(!o.compare.empty())parity=reference::compareSpectrum(state,o.compare);
    }
    if(!o.fields.empty()){auto snapshot=solver.snapshot(true);if(!snapshot)throw std::runtime_error("No snapshot available.");writeArray(o.fields,solver.readFrame(*snapshot));}
    return {{"mode","benchmark"},{"n",o.config.n},{"padded_n",o.config.m()},{"seed",o.config.seed},
        {"reynolds",o.config.reynolds},{"k0",o.config.k0},{"cfl",o.config.cfl},{"integrator",turbo::methods[int(o.config.integrator)]},
        {"operator",turbo::forms[int(o.config.form)]},{"start_spectrum",turbo::startSpectra[int(o.config.startSpectrum)]},
        {"fixed_dt",o.config.fixedDt},{"batch",o.batch},{"warmup",o.warmup},{"steps",o.steps},
        {"completed_timed_steps",final.step-before.step},{"completed_total_steps",final.step},
        {"initialization_ms",initialization*1000},{"wall_seconds",wall},{"gpu_event_ms",gpuMilliseconds},
        {"ms_per_step",wall*1000/o.steps},{"gpu_ms_per_step",gpuMilliseconds/o.steps},{"steps_per_second",o.steps/wall},
        {"initial_stats",reference::statistics(initial)},{"timed_initial_stats",reference::statistics(before)},{"stats",reference::statistics(final)},
        {"spectrum_comparison",parity}};
}
}
int main(int argc,char** argv) {
    try {
        const auto o=parse(argc,argv);
        if(o.help) {
            std::cout<<"FastTurbo native Linux CUDA/VkFFT reference (no desktop or Python runtime)\n"
                "--benchmark (default; validates recorded Windows trajectory before timing)\n"
                "--validate (validation only) --json --output FILE --device INDEX\n"
                "--n N --steps K --warmup K --seed UINT64 --batch 1..256\n"
                "--reynolds R --k0 K --cfl C --fixed-dt DT\n"
                "--integrator imexRK3|ifHeunRK3|etdrk4 --operator stress|advective\n"
                "--start-spectrum PAO|KM3 (default PAO)\n"
                "--initial FILE --spectrum FILE --fields FILE --compare-spectrum FILE\n"
                "--reference-root REPOSITORY (location of existing parity evidence)\n"
                "Defaults: N1024/M1536, 1000 timed steps, 100 warmup, seed=2, ETDRK4/stress, CFL=3.\n"
                "Legacy: SCIPYTURBO_SEED=1 fastturbo_reference N Re K0 Steps CFL cuda Update PAO|KM3 CNAB2\n"
                "        optional --device INDEX --output JSON --dump DIRECTORY\n";return 0;
        }
        auto hardware=deviceInfo(o.device);
        if(o.positional){printDevice(hardware);auto result=legacyRun(o,hardware);writeJSON(o.output,result);return 0;}
        if(!o.json)printDevice(hardware);
        else std::cerr<<"CUDA/VkFFT: "<<hardware["gpu"].get<std::string>()<<" (device "<<o.device<<")\n";
        auto parity=reference::validate(o.root);
        Json result={{"schema_version",1},{"hardware",hardware},{"parity",parity}};
        if(parity["status"]!="PASS") {
            writeJSON(o.output,result);std::cout<<result.dump(2)<<'\n';return 1;
        }
        if(o.benchmark)result.update(benchmark(o));else result["mode"]="validation";
        writeJSON(o.output,result);
        if(o.json)std::cout<<result.dump(2)<<'\n';
        else {
            std::cout<<"Parity: "<<parity["status"]<<" (recorded Windows trajectory + native state checks)\n";
            if(o.benchmark)std::cout<<"N: "<<o.config.n<<"  Padded: "<<o.config.m()<<"  Seed: "<<o.config.seed<<"  Warmup: "<<o.warmup<<"  Steps: "<<o.steps
                <<"\nInitialization: "<<result["initialization_ms"]<<" ms\nSolver: "<<result["ms_per_step"]<<" ms/step\nThroughput: "<<result["steps_per_second"]<<" steps/s\nGPU event interval: "<<result["gpu_ms_per_step"]<<" ms/step\n";
            if(result.contains("spectrum_comparison")&&!result["spectrum_comparison"].is_null())
                std::cout<<"Spectrum comparison: "<<result["spectrum_comparison"].dump()<<'\n';
        }
        return result.contains("spectrum_comparison")&&!result["spectrum_comparison"].is_null()&&result["spectrum_comparison"]["status"]!="PASS"?1:0;
    }catch(const std::exception& e){std::cerr<<"FastTurbo reference: "<<e.what()<<'\n';return 2;}
}
