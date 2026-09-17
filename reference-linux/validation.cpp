#include "reference.hpp"
#include <complex>
#include <fstream>
#include <numbers>
#include <regex>

namespace reference {
Json statistics(const turbo::Statistics& s) {
    return {{"steps",s.step},{"simulation_time",s.time},{"dt",s.dt},{"energy",s.energy},
        {"enstrophy",s.enstrophy},{"palinstrophy",s.palinstrophy},{"initial_turnover_time",s.turnover},
        {"allocated_bytes",s.allocatedBytes},{"vkfft_bytes",s.fftBytes}};
}
namespace {
struct Errors {
    double maxAbs=0,maxRel=0,squares=0;size_t count=0;bool pass=true;
    void add(double actual,double expected,double rtol,double atol) {
        double delta=std::abs(actual-expected);
        if(!std::isfinite(actual)||!std::isfinite(expected))throw std::runtime_error("Nonfinite parity sample.");
        maxAbs=std::max(maxAbs,delta);
        if(expected!=0)maxRel=std::max(maxRel,delta/std::abs(expected));
        squares+=delta*delta;++count;pass&=delta<=atol+rtol*std::abs(expected);
    }
    Json json()const{return {{"max_abs",maxAbs},{"max_rel",maxRel},{"rms",std::sqrt(squares/std::max(size_t(1),count))},
        {"samples",count},{"relative_error_convention","max over nonzero reference values; zeros tested with absolute tolerance"},{"status",pass?"PASS":"FAIL"}};}
};
void require(bool condition,const char* message){if(!condition)throw std::runtime_error(message);}
double radius(const std::string& value) {
    auto e=value.find_first_of("eE");auto dot=value.find('.');
    int exponent=e==std::string::npos?0:std::stoi(value.substr(e+1));
    int decimals=dot==std::string::npos?0:int((e==std::string::npos?value.size():e)-dot-1);
    return .5*std::pow(10.,exponent-decimals);
}
Json legacyParity(const std::filesystem::path& root) {
    const auto path=root/"reference-macos/2DFastTurbo/tools/sim_512_1001_metal_macOS.txt";
    std::ifstream input(path);if(!input)throw std::runtime_error("Missing legacy parity log: "+path.string());
    std::map<int,std::map<std::string,std::string>> reference;
    const std::regex field(R"(\b(T|DT|CN|CFLM|TAU_L)\s*=\s*([-+0-9.eE]+))"),iteration(R"(ITERATION\s+(\d+))"),energyPattern(R"(Energy\s*=\s*([-+0-9.eE]+))");
    std::string line,energy;std::smatch match;
    while(std::getline(input,line)) {
        if(std::regex_search(line,match,energyPattern))energy=match[1];
        int key=-2;
        if(std::regex_search(line,match,iteration))key=std::stoi(match[1]);
        else if(line.find("[NEXTDT INIT]")!=std::string::npos)key=0;
        else if(line.find("Final T=")!=std::string::npos)key=-1;
        if(key==-2)continue;
        for(auto i=std::sregex_iterator(line.begin(),line.end(),field);i!=std::sregex_iterator();++i)reference[key][(*i)[1]]=(*i)[2];
    }
    require(reference.size()==14&&!energy.empty(),"Canonical legacy log is incomplete.");
    turbo::legacy::Config config;turbo::legacy::Solver solver(config);Errors errors;Json differences=Json::array();
    auto compare=[&](const std::string& checkpoint,const std::string& name,double actual,const std::string& expected) {
        double value=std::stod(expected);
        // Existing compare_sim.py contract: 1e-5 + 5e-10 and printed rounding.
        errors.add(actual,value,1.e-5,5.e-10+radius(expected));
        differences.push_back({{"checkpoint",checkpoint},{"field",name},{"reference",value},{"actual",actual},{"difference",actual-value}});
    };
    compare("initial","Energy",solver.initial().energy,energy);
    auto initial=solver.initialRow();
    for(const auto& [name,value]:reference.at(0))compare("initial",name,name=="DT"?initial.dt:name=="CN"?initial.cn:initial.cflMaximum,value);
    auto final=solver.advance([&](const auto& row){
        const std::map<std::string,double> actual={{"T",row.time},{"DT",row.dt},{"CN",row.cn},{"CFLM",row.cflMaximum},{"TAU_L",row.tau}};
        require(reference.at(row.iteration).size()==actual.size(),"Missing legacy checkpoint fields.");
        for(const auto& [name,value]:reference.at(row.iteration))compare(std::to_string(row.iteration),name,actual.at(name),value);
    });
    for(const auto& [name,value]:reference.at(-1))compare("final",name,name=="T"?final.stateTime:name=="DT"?final.dt:final.cn,value);
    require(final.iteration==1001&&solver.initial().seedOut==149,"Legacy step count or PAO seed progression mismatch.");
    for(auto z:solver.spectrum())require(std::isfinite(z.x)&&std::isfinite(z.y),"Nonfinite CNAB2 spectrum.");
    auto result=errors.json();result["case"]="legacy N512/1001 PAO/CNAB2 seed1";result["reference"]=path.string();
    result["rtol"]=1.e-5;result["atol"]=5.e-10;result["printed_rounding_radius"]=true;
    result["differences"]=differences;result["scope"]="scalar trajectory; no stored macOS full-state fixture exists";return result;
}
Json nativeStateChecks() {
    turbo::Config c;c.n=32;c.fixedDt=.002f;turbo::Solver solver(c);
    auto initial=solver.spectrum();solver.advance(17);auto evolved=solver.spectrum();solver.reset();
    require(initial==solver.spectrum()&&initial!=evolved,"Deterministic reset or actual state evolution failed.");
    solver.advance(17);require(evolved==solver.spectrum(),"Deterministic trajectory failed.");
    std::vector<float> field(32*32);
    for(int y=0;y<32;++y)for(int x=0;x<32;++x)field[y*32+x]=float(20*std::cos(2*std::numbers::pi*(6*x+8*y)/32));
    Errors errors;
    // Independent single Fourier mode: nonlinearity vanishes, diffusion is exact.
    // Exercise all six production combinations, using the existing 3 ppm bound.
    for(int method=0;method<3;++method)for(int form=0;form<2;++form) {
        c.integrator=turbo::Integrator(method);c.form=turbo::Form(form);c.reynolds=10000;
        turbo::Solver core(c,field);auto stats=core.advance(17);core.inspect();auto spectrum=core.spectrum();
        require(stats.step==17&&stats.time>0,"No actual CUDA steps executed.");
        double expected=10*std::exp(-100*stats.time/c.reynolds);
        for(size_t i=0;i<spectrum.size();++i) {
            bool peak=i==8*32+6||i==24*32+26;
            errors.add(spectrum[i].x,peak?expected:0,3.e-6,1.e-6);
            errors.add(spectrum[i].y,0,3.e-6,1.e-6);
        }
    }
    auto result=errors.json();result["case"]="all six production methods/forms: Fourier diffusion + full finite state + deterministic reset";
    result["reference"]="analytic cosine decay (3 ppm; absolute float32 FFT noise <=1e-6)";
    return result;
}
}
Json compareSpectrum(const std::vector<turbo::Complex>& actual,const std::filesystem::path& path) {
    if(std::filesystem::file_size(path)!=actual.size()*sizeof(turbo::Complex))throw std::invalid_argument("Reference spectrum size differs.");
    std::vector<turbo::Complex> expected(actual.size());std::ifstream in(path,std::ios::binary);
    if(!in.read(reinterpret_cast<char*>(expected.data()),std::streamsize(expected.size()*sizeof(turbo::Complex))))throw std::runtime_error("Cannot read spectrum.");
    double squares=0,referenceSquares=0,maximum=0,relative=0;
    for(size_t i=0;i<actual.size();++i) {
        const auto a=actual[i],b=expected[i];
        double diff=std::hypot(double(a.x)-b.x,double(a.y)-b.y),magnitude=std::hypot(b.x,b.y);
        require(std::isfinite(diff)&&std::isfinite(magnitude),"Nonfinite spectrum comparison.");
        maximum=std::max(maximum,diff);relative=std::max(relative,diff/std::max(magnitude,1.e-30));
        squares+=diff*diff;referenceSquares+=magnitude*magnitude;
    }
    double l2=std::sqrt(squares/std::max(referenceSquares,1.e-60));
    bool pass=l2<3.e-6;
    // Same selected modes and tolerances as test_python_swift_trajectory_parity.
    size_t n=size_t(std::sqrt(actual.size()));
    for(size_t i:{size_t(0),size_t(1),n+2,n*n-1}) {
        auto a=actual.at(i),b=expected.at(i);
        pass&=std::hypot(double(a.x)-b.x,double(a.y)-b.y)<=1.e-8+3.e-6*std::hypot(b.x,b.y);
    }
    return {{"reference",path.string()},{"max_abs",maximum},{"max_rel",relative},
        {"rms",std::sqrt(squares/actual.size())},{"relative_l2",l2},{"relative_l2_tolerance",3.e-6},{"status",pass?"PASS":"FAIL"}};
}
Json validate(const std::filesystem::path& root) {
    auto path=root/"reference-windows/benchmarks/2026-09-13/headless-1.json";
    std::ifstream input(path);if(!input)throw std::runtime_error("Missing Windows parity data: "+path.string()+" (use --reference-root)");
    Json recorded=Json::parse(input);auto config=recorded.at("config");turbo::Config c;
    c.n=config.at("resolution");c.k0=config.at("k0");c.reynolds=config.at("reynolds");c.seed=config.at("seed");c.cfl=config.at("cfl");
    require(config.at("integrator")=="etdrk4"&&config.at("operator")=="stress"&&config.at("fixed_dt").is_null(),"Windows parity configuration changed; review validation.");
    turbo::Solver solver(c);auto initial=solver.inspect();solver.advance(recorded.at("stats").at("steps").get<int>());auto final=solver.inspect();
    Errors errors;Json differences=Json::array();
    for(const auto& [phase,actual]:std::vector<std::pair<std::string,Json>>{{"initial_stats",statistics(initial)},{"stats",statistics(final)}}) {
        auto expected=recorded.at(phase);require(actual.at("steps")==expected.at("steps"),"Windows parity step count differs.");
        for(auto key:{"simulation_time","dt","energy","enstrophy","palinstrophy","initial_turnover_time"}) {
            double a=actual.at(key),b=expected.at(key);
            // pytest.approx uses max(abs, rel*|expected|), not their sum.
            errors.add(a,b,0,std::max(1.e-9,3.e-6*std::abs(b)));
            differences.push_back({{"phase",phase},{"field",key},{"actual",a},{"reference",b},{"difference",a-b}});
        }
    }
    for(auto z:solver.spectrum())require(std::isfinite(z.x)&&std::isfinite(z.y),"Nonfinite final production spectrum.");
    auto windows=errors.json();windows["case"]="N1024/M1536, 3832 ETDRK4/stress steps, seed2, Re50000, CFL3";
    windows["reference"]=path.string();windows["rtol"]=3.e-6;windows["atol"]=1.e-9;windows["differences"]=differences;
    windows["scope"]="scalar trajectory; no stored Windows full-state fixture exists";
    Json cases=Json::array({windows,nativeStateChecks(),legacyParity(root)});
    bool pass=true;for(const auto& result:cases)pass&=result.at("status")=="PASS";
    return {{"status",pass?"PASS":"FAIL"},{"cases",cases}};
}
}
