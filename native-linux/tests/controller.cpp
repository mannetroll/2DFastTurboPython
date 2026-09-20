#include "controller.hpp"
#include "cuda_loader.hpp"
#include "cuda_support.cuh"
#include <chrono>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <thread>
#include <unistd.h>

namespace {
void require(bool ok,const char* message){if(!ok)throw std::runtime_error(message);}
template<class Predicate>turbo::ViewState wait(turbo::Controller& controller,Predicate predicate){
    double end=turbo::nowSeconds()+30;
    do{
        auto s=controller.view();require(s.error.empty(),s.error.c_str());if(predicate(s))return s;
        std::this_thread::sleep_for(std::chrono::milliseconds(5));
    }while(turbo::nowSeconds()<end);
    throw std::runtime_error("Controller timed out");
}
}
int main(){
    try{
        unsetenv("DISPLAY");unsetenv("WAYLAND_DISPLAY");
        initializeCudaLoader();CUDA_CHECK(cudaSetDevice(0));
        for(auto method:{turbo::Integrator::imexRK3,turbo::Integrator::ifHeunRK3,turbo::Integrator::etdrk4,turbo::Integrator::cnab2}){
            turbo::Options o;o.config.n=32;o.config.reynolds=50000;o.config.integrator=method;
            if(method==turbo::Integrator::cnab2)o.config.cfl=.25f;
            o.paused=true;o.regulated=false;
            turbo::Controller controller(o,0);controller.visualizationInterval(.2);
            auto s=wait(controller,[](auto& v){return v.ready&&v.settled&&v.frame;});
            require(s.stats.step==0,"Initialization advanced simulation");
            controller.singleStep();s=wait(controller,[](auto& v){return v.settled&&v.stats.step==1;});
            turbo::Solver reference(o.config);reference.advance(1);auto expected=reference.inspect();
            require(s.stats.time==expected.time&&s.stats.energy==expected.energy&&s.stats.enstrophy==expected.enstrophy,
                    "Controller differs from direct production solver");
            controller.visible(false);controller.running(true);
            s=wait(controller,[](auto& v){return v.stats.step>20;});controller.running(false);
            s=wait(controller,[](auto& v){return v.settled&&!v.running;});auto steps=s.stats.step;
            std::this_thread::sleep_for(std::chrono::milliseconds(80));
            require(controller.view().stats.step==steps,"Pause advanced simulation");
            controller.reset();s=wait(controller,[](auto& v){return v.ready&&v.settled&&v.stats.step==0;});
            require(!s.frame,"Hidden reset retained stale frame");
            controller.velocity(true);controller.visible(true);
            s=wait(controller,[](auto& v){return v.frame&&v.frame->hasVelocity;});
            require(s.stats.step==0,"Showing velocity advanced paused simulation");
        }
        // Snapshot cadence is independent of GUI polling and monitor refresh.
        turbo::Options o;o.config.n=128;o.regulated=false;
        turbo::Controller c(o,0);c.visualizationInterval(.25);
        wait(c,[](auto& s){return s.ready&&s.stats.step>20;});auto before=c.view();
        std::this_thread::sleep_for(std::chrono::milliseconds(800));auto after=c.view();
        require(after.stats.step>before.stats.step+20,"No autonomous simulation progress");
        require(after.snapshots-before.snapshots>=2&&after.snapshots-before.snapshots<=5,"Snapshot interval ignored");
        c.visible(false);std::this_thread::sleep_for(std::chrono::milliseconds(100));before=c.view();
        std::this_thread::sleep_for(std::chrono::milliseconds(300));after=c.view();
        require(after.snapshots==before.snapshots&&after.stats.step>before.stats.step,"Disabled visualization still extracts fields");
        bool rejected=false;try{c.visualizationInterval(0);}catch(const std::invalid_argument&){rejected=true;}
        require(rejected,"Invalid interval accepted");
        std::cout<<"PASS: all four methods match the direct solver; transport, hidden reset, frame leases, cadence, autonomous stepping\n";
        return 0;
    }catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}
}
