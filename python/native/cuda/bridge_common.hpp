// Shared CUDA ABI conversion and explicit export helpers. No window-system dependencies.
#pragma once
#include "options.hpp"
#include "cuda_support.cuh"
#include "json.hpp"
#include <cstring>
#include <cstdlib>
#include <fstream>
#include <mutex>
using namespace turbo;
using Json = nlohmann::json;
namespace {
// Selected before constructing any core. The benchmark uses an isolated process
// per case; ordinary GUI initialization retains its existing adapter selection.
int requestedDevice() {
    const char* value=std::getenv("FASTTURBO_DEVICE");
    if(!value)return 0;
    std::string s(value);
    if(s.empty()||s.find_first_not_of("0123456789")!=std::string::npos)
        throw std::invalid_argument("FASTTURBO_DEVICE must be a nonnegative device index.");
    return std::stoi(s);
}
void deviceMetadata(Json& info, int device) {
    cudaDeviceProp prop{};CUDA_CHECK(cudaGetDeviceProperties(&prop,device));
    size_t available=0,total=0;CUDA_CHECK(cudaMemGetInfo(&available,&total));
    info["device_index"]=device;
    info["compute_capability"]=std::to_string(prop.major)+"."+std::to_string(prop.minor);
    info["available_memory_bytes"]=available;
    info["precision"]="float32";
}
constexpr const char* fieldNames[] = {"vorticity", "speed", "enstrophy"};
constexpr const char* paletteNames[] = {"aurora", "ember", "glacier", "inferno"};
constexpr const char* rangeNames[] = {"sigma", "exposure"};
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
    auto previousIntegrator=c.integrator;
    c.integrator=Integrator(choice(j,"integrator",methods,int(c.integrator)));
    if(c.integrator==Integrator::cnab2&&previousIntegrator!=c.integrator&&!has(j,"cfl"))c.cfl=.25f;
    c.form=Form(choice(j,"operator",forms,int(c.form)));
    c.startSpectrum=StartSpectrum(choice(j,"start_spectrum",startSpectra,int(c.startSpectrum)));
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
        {"operator",forms[int(c.form)]},{"start_spectrum",startSpectra[int(c.startSpectrum)]},
        {"fixed_dt",c.fixedDt>0?Json(c.fixedDt):Json(nullptr)}};
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
struct EngineHandle {
    std::mutex mutex;
    Solver core;
    explicit EngineHandle(const Json& j):core(configuration(j),loadInitial([&]{Options o;o.config=configuration(j);o.initial=path(j,"initial");return o;}())){}
};
}
