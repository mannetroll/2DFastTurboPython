#include "controller.hpp"
#include "cuda_support.cuh"
namespace turbo {
Controller::Controller(const Options& o,int d):options(o),device(d) {
    request.config=o.config;request.running=!o.paused;request.velocity=o.display.velocity();request.regulated=o.regulated;
    if(!o.renderCheck.empty())request.velocity=true;
    request.measuring=!o.guiBenchmark;
    state.config=o.config;state.running=request.running;state.regulated=o.regulated;state.generation=1;
    worker=std::thread([this]{loop();});
}
Controller::~Controller(){ {std::lock_guard lock(mutex);request.stop=true;}changed.notify_one();worker.join();}
ViewState Controller::view()const{std::lock_guard lock(mutex);return state;}
void Controller::configure(Config c) {
    c.validate();std::lock_guard lock(mutex);
    if((c.n!=request.config.n||c.k0!=request.config.k0)&&request.regulated)c.reynolds=initialReynolds(c.n,c.k0);
    request.config=c;++request.generation;request.singleSteps=0;
    bool running=request.running,regulated=request.regulated;state={};state.config=c;state.running=running;state.regulated=regulated;state.generation=request.generation;
    changed.notify_one();
}
void Controller::reset(){Config c;{std::lock_guard lock(mutex);c=request.config;}configure(c);}
void Controller::startMeasurement(){
    std::lock_guard lock(mutex);request.measuring=true;request.running=true;++request.generation;request.singleSteps=0;
    state={};state.config=request.config;state.regulated=request.regulated;state.running=true;state.generation=request.generation;changed.notify_one();
}
void Controller::running(bool value){std::lock_guard lock(mutex);request.running=value;if(!value)request.singleSteps=0;state.running=value;state.settled=false;state.finished=false;changed.notify_one();}
void Controller::singleStep(){std::lock_guard lock(mutex);request.running=false;++request.singleSteps;state.running=false;state.settled=false;state.finished=false;changed.notify_one();}
void Controller::live(float cfl,double re){std::lock_guard lock(mutex);request.config.cfl=cfl;if(!request.regulated)request.config.reynolds=re;++request.liveVersion;state.config.cfl=cfl;if(!request.regulated){state.config.reynolds=re;state.stats.reynolds=re;}changed.notify_one();}
void Controller::regulate(bool value){std::lock_guard lock(mutex);if(value==request.regulated)return;request.regulated=value;++request.regulationVersion;
    if(!value&&state.stats.reynolds>0)request.config.reynolds=state.stats.reynolds;
    state.config.reynolds=request.config.reynolds;state.regulated=value;++request.liveVersion;changed.notify_one();}
void Controller::visible(bool v){std::lock_guard lock(mutex);if(v!=request.visible){request.visible=v;++request.frameVersion;changed.notify_one();}}
void Controller::velocity(bool v){std::lock_guard lock(mutex);if(v!=request.velocity){request.velocity=v;++request.frameVersion;changed.notify_one();}}
void Controller::dismissError(){std::lock_guard lock(mutex);state.error.clear();}
void Controller::loop() {
    std::unique_ptr<Solver> core;
    uint64_t generation=0,liveVersion=0,regulationVersion=0,frameVersion=0;
    RunAverage average;Regulator regulator;
    Statistics stats,initial;
    double estimate=.001,nextFrame=0,lastInspect=0,measurementTime=0,measurementStep=0,rate=0,turnoverRate=0,stepsRate=0,gpu=0;
    int batch=1,batches=0,snapshots=0;
    bool failed=false,finished=false,needFrame=true,needInspection=true,previousRunning=false;
    std::vector<double> history;
    try {CUDA_CHECK(cudaSetDevice(device));}catch(const std::exception& e){std::lock_guard lock(mutex);state.error=e.what();state.running=false;return;}
    for(;;) {
        Request r;
        {std::lock_guard lock(mutex);r=request;if(r.stop)return;}
        try {
            if(r.generation!=generation) {
                generation=r.generation;failed=false;finished=false;average={};regulator={};rate=turnoverRate=stepsRate=gpu=0;
                batch=1;batches=snapshots=0;estimate=.001;nextFrame=lastInspect=0;history.clear();
                Options run=options;run.config=r.config;auto field=loadInitial(run);
                auto compatible=[&]{if(!core)return false;auto a=core->configuration(),b=r.config;a.cfl=b.cfl;a.reynolds=b.reynolds;return a==b;};
                if(compatible()){core->setReynolds(r.config.reynolds);core->setCFL(r.config.cfl);core->reset(field);}
                else {core.reset();core=std::make_unique<Solver>(r.config,field);}
                initial=stats=core->inspect();liveVersion=r.liveVersion;regulationVersion=r.regulationVersion;
                needFrame=needInspection=true;measurementTime=nowSeconds();measurementStep=0;previousRunning=false;
            }
            // Replacement runs and pause requests own intent, including during FFT planning.
            {std::lock_guard lock(mutex);if(request.generation!=generation)continue;r=request;}
            bool regulationChanged=r.regulationVersion!=regulationVersion;
            if(regulationChanged){regulator={};regulationVersion=r.regulationVersion;}
            if(r.liveVersion!=liveVersion){core->setCFL(r.config.cfl);if(!r.regulated||regulationChanged)core->setReynolds(r.config.reynolds);liveVersion=r.liveVersion;}
            if(r.frameVersion!=frameVersion){needFrame=true;frameVersion=r.frameVersion;}
            if(!r.running&&!previousRunning)average.stop(nowSeconds());
            if(r.running&&!previousRunning){measurementTime=nowSeconds();measurementStep=double(stats.step);finished=false;}
            previousRunning=r.running;
            if(!failed) {
                if(needInspection) {
                    stats=core->inspect();lastInspect=nowSeconds();needInspection=false;
                    if(r.regulated)if(auto re=regulator.next(stats.reynolds,stats.metric,r.config.n,r.config.k0)){core->setReynolds(*re);stats.reynolds=*re;}
                    if(history.empty()||history.back()!=stats.energy){history.push_back(stats.energy);if(history.size()>120)history.erase(history.begin());}
                }
                if(r.visible&&(needFrame||((r.running||r.singleSteps>0)&&nowSeconds()>=nextFrame))) {
                    auto frame=core->snapshot(r.velocity);
                    if(frame){std::lock_guard lock(mutex);if(request.generation!=generation)continue;state.frame=frame;state.ready=true;++snapshots;needFrame=false;}
                    double completed=nowSeconds();if(nextFrame==0)nextFrame=completed+1./60;
                    else if(nextFrame<=completed)nextFrame+=(std::floor((completed-nextFrame)*60)+1)/60;
                }
            }
            {std::lock_guard lock(mutex);
                if(request.generation!=generation)continue;
                state.stats=stats;if(!request.regulated)state.stats.reynolds=request.config.reynolds;
                state.initial=initial;state.history=history;state.wall=average.wall(nowSeconds());state.gpuSeconds=gpu;
                state.batches=batches;state.snapshots=snapshots;state.batchSteps=batch;state.finished=finished;
                if(request.running){state.rate=rate;state.turnoversRate=turnoverRate;state.stepsRate=stepsRate;}
            }
            if(failed||finished||(!r.running&&r.singleSteps==0)) {
                average.stop(nowSeconds());std::unique_lock lock(mutex);
                if(request.generation==generation)state.settled=true;
                changed.wait_for(lock,std::chrono::milliseconds(20),[&]{return request.stop||request.generation!=generation||request.running!=r.running||request.singleSteps>0||request.liveVersion!=liveVersion||request.regulationVersion!=regulationVersion||request.frameVersion!=frameVersion;});
                continue;
            }
            bool single=r.singleSteps>0;
            if(single){std::lock_guard lock(mutex);if(request.generation!=generation)continue;--request.singleSteps;}
            int count=single?1:options.fixedBatch?options.batch:batch;
            if(!single&&!options.fixedBatch&&r.visible&&nextFrame>0)count=std::min(count,std::max(1,int(std::max(0.,nextFrame-nowSeconds())/estimate)));
            auto endpoint=options.until;if(options.turnovers)endpoint=*options.turnovers*initial.turnover;
            if(!r.measuring)endpoint.reset();
            bool stepEndpoint=options.guiBenchmark&&r.measuring&&!endpoint&&options.wallSeconds==0;
            if(stepEndpoint)count=std::min(count,std::max(0,options.steps-int(stats.step)));
            double started=nowSeconds(),before=stats.time;average.begin(!single,started);
            stats=core->advance(count,single?std::nullopt:endpoint);double completed=nowSeconds();
            average.complete(before,stats.time,!single);gpu+=stats.gpuMilliseconds/1000;++batches;
            estimate=.8*estimate+.2*std::max((completed-started)/std::max(count,1),.000001);
            batch=std::min(std::clamp(int(.008/estimate),1,32),std::max(1,batch*2));
            if(completed-started>.016)batch=std::max(1,std::min(batch,int(count*.008/(completed-started))));
            {std::lock_guard lock(mutex);if(request.generation!=generation)continue;
                if(!single&&r.measuring&&((endpoint&&reached(stats.time,*endpoint))||(stepEndpoint&&stats.step>=uint64_t(options.steps))||(options.wallSeconds>0&&average.wall(completed)>=options.wallSeconds))) {
                    finished=true;request.running=false;state.running=false;
                    writeSpectrum(*core,options);
                }
                if(!request.running){average.stop(completed);previousRunning=false;}
                needInspection=completed-lastInspect>=.25||single||finished||!request.running;
            }
            if(completed-measurementTime>=.25&&!single) {
                rate=average.rate(completed);turnoverRate=stats.turnover>0?rate/stats.turnover:0;
                stepsRate=(double(stats.step)-measurementStep)/(completed-measurementTime);
                measurementTime=completed;measurementStep=double(stats.step);
            }
            needFrame=needFrame||completed>=nextFrame||single||finished;
        }catch(const std::exception& e) {
            failed=true;average.stop(nowSeconds());std::lock_guard lock(mutex);
            if(request.generation!=generation)continue;
            state.error=e.what();state.running=false;state.settled=true;request.running=false;request.singleSteps=0;
        }
    }
}
}
