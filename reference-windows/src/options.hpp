#pragma once
#include "solver.hpp"
#include <filesystem>
#include <string>
namespace turbo {
struct Options {
    Config config;
    Display display;
    bool benchmark=false, guiBenchmark=false, liveCheck=false, paused=false, regulated=true, help=false, licenses=false;
    int steps=200, batch=8, warmup=8;
    bool fixedBatch=false;
    double wallSeconds=0, snapshotHz=0, guiWarmup=2, canvasSide=640;
    std::optional<double> until,turnovers;
    std::filesystem::path initial,spectrum,fields,output,exportImage,capture,renderCheck;
};
double nowSeconds();
std::vector<float> loadInitial(const Options& options);
std::string reportJSON(const Options&,const Statistics& initial,const Statistics& final,double seconds,
                       double gpuSeconds,int batches,int snapshots,const char* mode,double fps=0);
void writeReport(const Options&,const std::string& json);
void writeSpectrum(Solver&,const Options&);
int runApp(const Options&);
}
