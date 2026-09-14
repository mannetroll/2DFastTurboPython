#pragma once
#include "options.hpp"
#include <condition_variable>
#include <mutex>
#include <thread>
namespace turbo {
struct ViewState {
    Config config;
    Statistics stats,initial;
    std::shared_ptr<Frame> frame;
    uint64_t generation=0;
    bool ready=false,running=true,settled=false,regulated=true,finished=false;
    double rate=0,turnoversRate=0,stepsRate=0,wall=0,gpuSeconds=0;
    int batches=0,snapshots=0,batchSteps=1;
    std::vector<double> history;
    std::string error;
};
class Controller {
    struct Request {
        Config config;
        uint64_t generation=1,liveVersion=0,regulationVersion=0,frameVersion=0;
        bool running=true,visible=true,velocity=false,regulated=true,stop=false,measuring=true;
        int singleSteps=0;
    } request;
    mutable std::mutex mutex;
    std::condition_variable changed;
    ViewState state;
    Options options;
    int device;
    std::thread worker;
    void loop();
public:
    Controller(const Options& options,int device);
    ~Controller();
    ViewState view() const;
    void configure(Config config);
    void reset();
    void startMeasurement();
    void running(bool value);
    void singleStep();
    void live(float cfl,double re);
    void regulate(bool enabled);
    void visible(bool value);
    void velocity(bool value);
    void dismissError();
};
}
