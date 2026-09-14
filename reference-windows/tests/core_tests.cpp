#include "solver.hpp"
#include <iostream>
#include <numbers>
#include <functional>
using namespace turbo;
static void require(bool condition,const char* message){if(!condition)throw std::runtime_error(message);}
static void rejects(const std::function<void()>& f){bool threw=false;try{f();}catch(const std::exception&){threw=true;}require(threw,"Expected validation failure");}
int main(){
try {
    require(std::abs(initialReynolds(1024,4)-27898.36614974335)<1e-8,"Initial Reynolds calibration");
    RunAverage average;average.begin(true,10);average.complete(0,2,true);average.begin(true,14);average.complete(2,6,true);
    require(std::abs(average.rate(15)-1.2)<1e-12,"Whole-run weighted throughput");average.stop(15);
    average.begin(false,100);average.complete(6,106,false);require(average.rate(150)==1.2,"Manual step throughput exclusion");
    average.begin(true,200);average.complete(106,108,true);require(average.rate(203)==1,"Resume throughput");
    Regulator pid;auto re=pid.next(50000,.003,1024,4);require(re&&*re>50000,"Regulator sign");
    Config c;c.n=32;c.fixedDt=.002f;
    for(auto bad:{8,31,4098}){auto x=c;x.n=bad;rejects([&]{Solver core(x);});}
    Solver core(c);auto initial=core.spectrum();auto a=core.snapshot(),b=core.snapshot(),d=core.snapshot();
    require(a&&b&&d&&!core.snapshot(),"Snapshot pool must coalesce when occupied");
    auto bytes=core.readFrame(*a);core.advance(8);require(core.readFrame(*a)==bytes,"Snapshot mutated after advance");
    b.reset();b=core.snapshot();require(b&&b->time>a->time,"Released snapshot slot not reused");
    core.reset();require(core.readFrame(*a)==bytes&&core.spectrum()==initial,"Deterministic reset/lease lifetime");
    require(core.inspect().step==0&&core.inspect().time==0,"Reset clock");
    a.reset();b.reset();d.reset();auto f=core.snapshot();require(core.readFrame(*f)==bytes,"Reset physical field");
    auto values=core.readFrame(*f);double mean=0,squares=0,maxw=0,maxv=0;size_t count=size_t(c.n)*c.n;
    for(size_t i=0;i<count;++i){mean+=values[i];squares+=double(values[i])*values[i];maxw=std::max(maxw,std::abs(double(values[i])));maxv=std::max(maxv,std::hypot(double(values[count+i]),double(values[2*count+i])));}
    mean/=count;double sigma=std::sqrt(squares/count-mean*mean);
    require(std::abs(f->mean-mean)<1e-6&&std::abs(f->sigma-sigma)<1e-5&&f->maxOmega==maxw&&std::abs(f->maxSpeed-maxv)<1e-6,"Snapshot reductions");
    auto z=core.advance(0);require(z.step==0&&z.time==0,"Zero-step must not advance");
    auto s=core.advance(1);require(s.step==1&&std::abs(s.time-.002)<1e-9,"Single completed timestep");
    auto end=core.advance(64,.012345);require(std::abs(end.time-.012345)<1e-13&&end.step==7,"Exact endpoint / inactive extra steps");
    std::cout<<"PASS defaults, regulator, throughput, validation, leases, reset, diagnostics, endpoints\n";
    // Exercise the actual six combinations, including live viscosity/CFL without reset.
    std::vector<float> field(32*32);for(int y=0;y<32;++y)for(int x=0;x<32;++x)field[y*32+x]=float(20*std::cos(2*std::numbers::pi*(6*x+8*y)/32));
    for(int method=0;method<3;++method)for(int form=0;form<2;++form){
        c.integrator=Integrator(method);c.form=Form(form);c.fixedDt=0;c.reynolds=10000;c.cfl=3;
        Solver live(c,field);live.advance(4);auto before=live.inspect();auto spectrum=live.spectrum();auto held=live.snapshot();auto heldBytes=live.readFrame(*held);
        live.setCFL(1);live.setReynolds(1000);auto changed=live.inspect();
        require(changed.step==before.step&&changed.time==before.time&&changed.turnover==before.turnover&&changed.allocatedBytes==before.allocatedBytes&&live.spectrum()==spectrum,"Live scalar edit reset state");
        auto after=live.advance(1);require(after.step==before.step+1&&after.dt<before.dt*.5,"CFL decrease must be immediate");
        auto evolved=live.spectrum();double expected=spectrum[8*32+6].x*std::exp(-.1*(after.time-before.time));
        require(std::abs(evolved[8*32+6].x-expected)<1e-4,"Live viscosity not applied");
        require(live.readFrame(*held)==heldBytes,"Live edit changed leased frame");
        rejects([&]{live.setCFL(0);});rejects([&]{live.setCFL(std::numeric_limits<float>::infinity());});rejects([&]{live.setReynolds(0);});
        std::cout<<"PASS live "<<methods[method]<<" / "<<forms[form]<<'\n';
    }
    c.n=32;c.reynolds=std::numeric_limits<double>::infinity();c.fixedDt=std::numeric_limits<float>::max();c.form=Form::advective;
    for(int y=0;y<32;++y)for(int x=0;x<32;++x)field[y*32+x]=float(10*std::cos(2*std::numbers::pi*x/32)+10*std::cos(4*std::numbers::pi*y/32));
    Solver failing(c,field);auto pristine=failing.spectrum();rejects([&]{failing.advance(1);});
    rejects([&]{failing.advance(0);});rejects([&]{failing.inspect();});rejects([&]{failing.snapshot();});rejects([&]{failing.spectrum();});
    rejects([&]{failing.reset({0});});rejects([&]{failing.spectrum();});failing.reset(field);
    require(failing.inspect().step==0&&failing.spectrum()==pristine,"Successful reset must recover failure");
    std::cout<<"PASS terminal failure latch and reset recovery\n";return 0;
}catch(const std::exception& e){std::cerr<<"FAIL: "<<e.what()<<'\n';return 1;}
}
