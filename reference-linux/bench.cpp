#include "bench_build.hpp"
#include "cuda_loader.hpp"
#include "cuda_support.cuh"
#include "solver.hpp"
#include "json.hpp"
#include <cerrno>
#include <charconv>
#include <chrono>
#include <cctype>
#include <cstring>
#include <ctime>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <sstream>
#include <string_view>
#include <sys/utsname.h>
#include <sys/wait.h>
#include <unistd.h>

namespace {
using Json = nlohmann::json;
using Clock = std::chrono::steady_clock;
constexpr int batchSteps = 8;
constexpr double reynolds = 27898.36614974335;
constexpr const char* timing = "synchronized wall time, including batch submission and final inspection; initialization/warmup/reset excluded";

struct Options {
    std::vector<int> sizes{2048, 4096};
    double seconds = 20;
    int device = 0, warmup = 8;
    bool json = false, help = false;
};

int integer(std::string_view value, std::string_view name, int limit) {
    unsigned int result = 0;
    auto [end, error] = std::from_chars(value.data(), value.data() + value.size(), result);
    if (error != std::errc{} || end != value.data() + value.size() || result > unsigned(limit))
        throw std::invalid_argument(std::string(name) + " requires an integer between 0 and " + std::to_string(limit));
    return int(result);
}

Options parse(int argc, char** argv) {
    Options options;
    for (int i = 1; i < argc; ++i) {
        std::string key = argv[i], attached;
        bool hasAttached = false;
        if (auto equals = key.find('='); equals != std::string::npos) {
            attached = key.substr(equals + 1);
            key.resize(equals);
            hasAttached = true;
        }
        auto value = [&]() -> std::string {
            if (hasAttached) return attached;
            if (i + 1 == argc) throw std::invalid_argument(key + " requires a value");
            return argv[++i];
        };
        if (key == "--seconds") {
            auto text = value();
            double seconds = 0;
            auto [end, error] = std::from_chars(text.data(), text.data() + text.size(), seconds);
            if (error != std::errc{} || end != text.data() + text.size() || !std::isfinite(seconds) || seconds <= 0)
                throw std::invalid_argument("--seconds must be finite and positive");
            options.seconds = seconds;
        } else if (key == "--device") {
            options.device = integer(value(), key, std::numeric_limits<int>::max());
        } else if (key == "--warmup") {
            options.warmup = integer(value(), key, 4096);
        } else if (key == "--sizes") {
            options.sizes.clear();
            auto append = [&](const std::string& text) {
                int n = integer(text, key, 16384);
                if (n < 16 || n % 2) throw std::invalid_argument("--sizes must be even, between 16 and 16384");
                options.sizes.push_back(n);
            };
            append(value());
            while (!hasAttached && i + 1 < argc && !std::string_view(argv[i + 1]).starts_with("-"))
                append(argv[++i]);
        } else if (key == "--json" && !hasAttached) {
            options.json = true;
        } else if ((key == "--help" || key == "-h") && !hasAttached) {
            options.help = true;
        } else {
            throw std::invalid_argument("Unknown option: " + key);
        }
    }
    return options;
}

std::string timestamp() {
    auto now = std::time(nullptr);
    std::tm utc{};
    gmtime_r(&now, &utc);
    std::ostringstream out;
    out << std::put_time(&utc, "%Y-%m-%dT%H:%M:%SZ");
    return out.str();
}

Json platformMetadata() {
    utsname system{};
    if (uname(&system) != 0) throw std::runtime_error("Cannot read Linux system information");
    std::string os = std::string(system.sysname) + " " + system.release, line;
    std::ifstream release("/etc/os-release");
    while (std::getline(release, line)) {
        if (line.starts_with("PRETTY_NAME=")) {
            std::istringstream value(line.substr(12));
            if (value.peek() == '"') value >> std::quoted(os);
            else std::getline(value, os);
        }
    }
    Json architectures = Json::array();
    std::istringstream arch(bench_build::architectures);
    while (std::getline(arch, line, ';')) architectures.push_back(line);
    return {{"hostname", system.nodename}, {"os", os}, {"arch", system.machine},
        {"gpu", nullptr}, {"device_index", nullptr}, {"backend", "CUDA + VkFFT"},
        {"compute_capability", nullptr}, {"gpu_memory_bytes", nullptr},
        {"physical_memory_bytes", nullptr}, {"unified_memory", false},
        {"recommended_working_set_bytes", nullptr}, {"cuda_runtime", nullptr}, {"cuda_driver", nullptr},
        {"vkfft_version", bench_build::vkfft}, {"cuda_build_architectures", architectures},
        {"precision", "float32"}, {"git_commit", std::string_view(bench_build::commit) == "unknown" ? Json(nullptr) : Json(bench_build::commit)},
        {"git_dirty", Json::parse(bench_build::dirty)}, {"git_metadata_source", "cmake-configure"}};
}

Json deviceMetadata(int index) {
    initializeCudaLoader();
    int count = 0;
    CUDA_CHECK(cudaGetDeviceCount(&count));
    if (index >= count) throw std::runtime_error("CUDA device index out of range (device count " + std::to_string(count) + ")");
    CUDA_CHECK(cudaSetDevice(index));
    CUDA_CHECK(cudaFree(nullptr));
    cudaDeviceProp device{};
    CUDA_CHECK(cudaGetDeviceProperties(&device, index));
    size_t available = 0, total = 0;
    CUDA_CHECK(cudaMemGetInfo(&available, &total));
    int runtime = 0, driver = 0;
    CUDA_CHECK(cudaRuntimeGetVersion(&runtime));
    CUDA_CHECK(cudaDriverGetVersion(&driver));
    auto version = [](int v) { return std::to_string(v / 1000) + "." + std::to_string(v % 1000 / 10); };
    return {{"gpu", device.name}, {"device_index", index},
        {"compute_capability", std::to_string(device.major) + "." + std::to_string(device.minor)},
        {"gpu_memory_bytes", device.totalGlobalMem}, {"available_memory_bytes", available},
        {"cuda_runtime", version(runtime)}, {"cuda_driver", version(driver)},
        {"backend", "CUDA + VkFFT"}, {"precision", "float32"}};
}

bool finite(const turbo::Statistics& s) {
    return std::isfinite(s.time) && std::isfinite(s.dt) && std::isfinite(s.energy) &&
        std::isfinite(s.enstrophy) && std::isfinite(s.palinstrophy);
}

Json statistics(const turbo::Statistics& s) {
    return {{"steps", s.step}, {"simulation_time", s.time}, {"dt", s.dt}, {"energy", s.energy},
        {"enstrophy", s.enstrophy}, {"palinstrophy", s.palinstrophy}, {"palinstrophy_ratio", s.metric},
        {"reynolds", s.reynolds}, {"initial_turnover_time", s.turnover}, {"gpu_milliseconds", s.gpuMilliseconds},
        {"allocated_bytes", s.allocatedBytes}, {"is_finite", finite(s)}};
}

double elapsed(Clock::time_point start) {
    return std::chrono::duration<double>(Clock::now() - start).count();
}

Json benchmark(const Options& options, int n, const Json& hardware) {
    const auto initializing = Clock::now();
    turbo::Config config;
    config.n = n;
    config.k0 = 10;
    config.reynolds = reynolds;
    turbo::Solver solver(config);
    if (options.warmup) {
        solver.advance(options.warmup);
        solver.reset();
    }
    const auto initial = solver.inspect();
    auto stats = initial;
    uint64_t batches = 0;
    double gpuSeconds = 0, finalBatch = 0;
    const auto start = Clock::now();
    const double initializationSeconds = std::chrono::duration<double>(start - initializing).count();
    // advance() synchronizes each batch; inspect() synchronizes the final
    // diagnostics. This matches fastturbo.main.run_benchmark's timing boundary.
    do {
        const auto batchStart = Clock::now();
        const auto before = stats;
        stats = solver.advance(batchSteps);
        ++batches;
        gpuSeconds += stats.gpuMilliseconds / 1000;
        if (!finite(stats) || stats.step - before.step != batchSteps || stats.time <= before.time)
            throw std::runtime_error("Solver stopped making finite progress");
        finalBatch = elapsed(batchStart);
    } while (elapsed(start) < options.seconds);
    const auto inspectionStart = Clock::now();
    stats = solver.inspect();
    const auto finish = Clock::now();
    const double seconds = std::chrono::duration<double>(finish - start).count();
    const auto steps = stats.step - initial.step;
    if (!finite(stats) || steps != batches * batchSteps || initial.step != 0 || !(gpuSeconds > 0))
        throw std::runtime_error("Invalid completed benchmark state");
    const double simulated = stats.time - initial.time;
    Json report = {{"schema_version", 1}, {"solver", "FastTurbo native Linux"}, {"mode", "headless"},
        {"config", {{"resolution", n}, {"padded_resolution", config.m()}, {"reynolds", config.reynolds},
            {"k0", config.k0}, {"seed", std::to_string(config.seed)}, {"cfl", config.cfl},
            {"integrator", turbo::methods[int(config.integrator)]}, {"operator", turbo::forms[int(config.form)]},
            {"fixed_dt", nullptr}, {"batch", batchSteps}}},
        {"initial_stats", statistics(initial)}, {"stats", statistics(stats)}, {"frame", nullptr},
        {"seconds", seconds}, {"initialization_seconds", initializationSeconds},
        {"simtime_per_s", simulated / seconds}, {"steps_per_s", steps / seconds},
        {"milliseconds_per_step", seconds * 1000 / steps},
        {"turnovers_per_s", simulated / std::max(initial.turnover * seconds, 1e-12)},
        {"gpu_seconds", gpuSeconds}, {"gpu_ms_per_step", gpuSeconds * 1000 / steps},
        {"batches", batches}, {"snapshots", 0}, {"snapshot_seconds", 0}, {"snapshot_hz", 0},
        {"warmup_steps", options.warmup}, {"target_wall_seconds", options.seconds}, {"target_time", nullptr},
        {"final_batch_seconds", finalBatch},
        {"final_inspection_seconds", std::chrono::duration<double>(finish - inspectionStart).count()},
        {"spectrum_normalization", "fourier-series"}, {"vorticity_convention", "omega = dx(v) - dy(u)"},
        {"initial_source", "seeded-pao"}, {"initial_sha256", nullptr}, {"hardware", hardware}, {"timestamp", timestamp()}};
    return {{"n", n}, {"padded_n", config.m()}, {"status", "ok"}, {"steps", steps}, {"seconds", seconds},
        {"ms_per_step", seconds * 1000 / steps}, {"steps_per_second", steps / seconds},
        {"initialization_seconds", initializationSeconds}, {"native_report", std::move(report)}};
}

Json failure(int n, std::string message) {
    std::string lower = message;
    std::transform(lower.begin(), lower.end(), lower.begin(), [](unsigned char c) { return std::tolower(c); });
    bool memory = false;
    for (const char* term : {"out of memory", "out_of_memory", "memory allocation", "cannot allocate",
            "insufficient device memory", "std::bad_alloc", "failed_to_allocate"})
        memory |= lower.find(term) != std::string::npos;
    if (lower.find("vkfft") != std::string::npos)
        memory |= lower.ends_with("failed: 4001") || lower.ends_with("failed: 4025");
    if (memory && lower.find("insufficient device memory") == std::string::npos)
        message = "insufficient device memory: " + message;
    return {{"n", n}, {"padded_n", 3 * n / 2}, {"status", memory ? "skipped" : "failed"}, {"reason", message}};
}

Json runCase(const Options& options, int n, bool metadataOnly) {
    Json hardware = Json::object(), result;
    try {
        hardware = deviceMetadata(options.device);
        if (metadataOnly) return {{"hardware", hardware}};
        // Same lower bound as fastturbo.bench; not a prediction of peak usage.
        const uint64_t padded = 3 * n / 2;
        const uint64_t minimum = 84ULL * n * n + 8ULL * padded * padded;
        const auto available = hardware.at("available_memory_bytes").get<uint64_t>();
        if (minimum > available) {
            std::ostringstream reason;
            reason << std::fixed << std::setprecision(2) << "insufficient device memory: at least "
                << minimum / double(1ULL << 30) << " GiB of solver buffers required; "
                << available / double(1ULL << 30) << " GiB available";
            result = failure(n, reason.str());
        } else {
            result = benchmark(options, n, hardware);
        }
    } catch (const std::exception& error) {
        result = failure(n, error.what());
    }
    return {{"hardware", hardware}, {"result", result}};
}

Json launchCase(const Options& options, int n, bool metadataOnly = false) {
    // The parent never initializes CUDA. Each fork owns a fresh context, so even
    // an allocation failure or crashed driver cannot poison the next grid size.
    int channel[2];
    auto failed = [&](const std::string& reason) -> Json {
        return {{"hardware", Json::object()}, {"result", failure(n, reason)}};
    };
    if (pipe(channel) != 0) return failed("Cannot create worker pipe: " + std::string(std::strerror(errno)));
    std::cout.flush();
    std::cerr.flush();
    const pid_t child = fork();
    if (child == -1) {
        const auto message = std::string(std::strerror(errno));
        close(channel[0]);
        close(channel[1]);
        return failed("Cannot start native worker: " + message);
    }
    if (child == 0) {
        close(channel[0]);
        // Native/compiler output must never corrupt the parent's JSON stdout.
        if (dup2(STDERR_FILENO, STDOUT_FILENO) == -1) _exit(2);
        try {
            const auto payload = runCase(options, n, metadataOnly).dump();
            size_t offset = 0;
            while (offset < payload.size()) {
                auto written = write(channel[1], payload.data() + offset, payload.size() - offset);
                if (written < 0 && errno == EINTR) continue;
                if (written <= 0) _exit(2);
                offset += size_t(written);
            }
            close(channel[1]);
            std::cout.flush();
            std::cerr.flush();
            _exit(0);
        } catch (...) {
            _exit(2);
        }
    }
    close(channel[1]);
    std::string payload;
    char buffer[4096];
    bool readError = false;
    for (;;) {
        auto count = read(channel[0], buffer, sizeof(buffer));
        if (count < 0 && errno == EINTR) continue;
        if (count < 0) readError = true;
        if (count <= 0) break;
        payload.append(buffer, size_t(count));
    }
    close(channel[0]);
    int status = 0;
    pid_t waited;
    do { waited = waitpid(child, &status, 0); } while (waited == -1 && errno == EINTR);
    if (waited == -1 || readError) return failed("Cannot collect native worker result");
    if (WIFSIGNALED(status)) return failed("Native worker terminated by signal " + std::to_string(WTERMSIG(status)));
    if (!WIFEXITED(status) || WEXITSTATUS(status) != 0)
        return failed("Native worker exited with code " + std::to_string(WIFEXITED(status) ? WEXITSTATUS(status) : -1));
    try {
        return Json::parse(payload);
    } catch (const Json::exception&) {
        return failed("Native worker returned invalid JSON; see stderr");
    }
}

void printHeader(const Json& report) {
    std::cout << "FastTurbo 2D Benchmark (native Linux)\n";
    for (auto [label, key] : {std::pair{"Host:", "hostname"}, {"OS:", "os"}, {"Arch:", "arch"},
            {"GPU:", "gpu"}, {"Device:", "device_index"}, {"Compute:", "compute_capability"}, {"Backend:", "backend"}}) {
        if (!report.at(key).is_null())
            std::cout << std::left << std::setw(12) << label
                << (report.at(key).is_string() ? report.at(key).get<std::string>() : report.at(key).dump()) << '\n';
    }
    std::cout << std::fixed;
    if (!report.at("gpu_memory_bytes").is_null())
        std::cout << "VRAM:       " << std::setprecision(1) << report.at("gpu_memory_bytes").get<double>() / (1ULL << 30) << " GiB\n";
    if (!report.at("cuda_runtime").is_null())
        std::cout << "CUDA:       " << report.at("cuda_runtime").get<std::string>() << " runtime / "
            << report.at("cuda_driver").get<std::string>() << " driver API\n";
    std::cout << "VkFFT:      " << bench_build::vkfft << "\nPrecision:  float32\nGit:        "
        << std::string(bench_build::commit).substr(0, 12) << (report.at("git_dirty") == true ? " (dirty)" : "")
        << " (build)\nWorkload:   ETDRK4 / stress, k0=10, seed=2, CFL=3, Re=" << std::setprecision(8) << reynolds
        << "\nTiming:     synchronized wall time, batch=8, warmup=" << report.at("warmup_steps") << " steps, then reset"
        << "\nTarget:     " << std::setprecision(1) << report.at("target_seconds").get<double>() << " s per case\n"
        << " Retained      Padded      Steps    Seconds    ms/step    steps/s\n" << std::string(64, '-') << '\n' << std::flush;
}

void printResult(const Json& row) {
    // UTF-8 squared is two bytes, hence one extra byte in the column width.
    std::cout << ' ' << std::left << std::setw(14) << (std::to_string(row.at("n").get<int>()) + "²")
        << std::setw(13) << (std::to_string(row.at("padded_n").get<int>()) + "²");
    if (row.at("status") == "ok") {
        std::cout << std::right << std::setw(7) << row.at("steps").get<uint64_t>()
            << std::fixed << std::setprecision(2) << std::setw(11) << row.at("seconds").get<double>()
            << std::setprecision(3) << std::setw(11) << row.at("ms_per_step").get<double>()
            << std::setprecision(1) << std::setw(11) << row.at("steps_per_second").get<double>();
    } else {
        std::cout << (row.at("status") == "skipped" ? "SKIPPED: " : "FAILED: ") << row.at("reason").get<std::string>();
    }
    std::cout << '\n' << std::flush;
}
}

int main(int argc, char** argv) {
    try {
        const auto options = parse(argc, argv);
        if (options.help) {
            std::cout << "Usage: 2dbench [--seconds SECONDS] [--sizes N ...] [--device INDEX] [--warmup STEPS] [--json]\n"
                "Native Linux CUDA/VkFFT architecture benchmark; no Python or desktop dependencies.\n"
                "  --seconds SECONDS  Timed seconds per size (default: 20; finite and positive)\n"
                "  --sizes N ...      Retained even sizes, 16..16384 (default: 2048 4096)\n"
                "  --device INDEX     CUDA visible-device index (default: 0)\n"
                "  --warmup STEPS     Untimed warmup steps, followed by reset (default: 8; 0..4096)\n"
                "  --json             Emit one JSON document\n"
                "  -h, --help         Show this help without initializing CUDA\n"
                "Workload: ETDRK4/stress, k0=10, seed=2, CFL=3, fixed Re=27898.36614974335, batch=8.\n"
                "Timing includes synchronized batches and final inspection; excludes initialization/warmup/reset.\n"
                "Exit codes: 0 all cases succeeded; 1 any failed/skipped case; 2 invalid arguments/setup.\n";
            return 0;
        }
        auto report = platformMetadata();
        report.update({{"schema_version", 1}, {"target_seconds", options.seconds}, {"warmup_steps", options.warmup},
            {"timing", timing}, {"batch_steps", batchSteps}, {"results", Json::array()}, {"timestep_ratio_4096_2048", nullptr}});
        if (!options.json) {
            const auto probe = launchCase(options, options.sizes.front(), true);
            report.update(probe.at("hardware"));
            printHeader(report);
        }
        bool ok = true, haveHardware = false;
        double ms2048 = 0, ms4096 = 0;
        for (int n : options.sizes) {
            const auto result = launchCase(options, n);
            if (!haveHardware && !result.at("hardware").empty()) {
                report.update(result.at("hardware"));
                haveHardware = true;
            }
            const auto& row = result.at("result");
            report["results"].push_back(row);
            if (row.at("status") != "ok") ok = false;
            else if (n == 2048) ms2048 = row.at("ms_per_step").get<double>();
            else if (n == 4096) ms4096 = row.at("ms_per_step").get<double>();
            if (!options.json) printResult(row);
        }
        report["timestamp"] = timestamp();
        if (ms2048 > 0 && ms4096 > 0) report["timestep_ratio_4096_2048"] = ms4096 / ms2048;
        if (options.json) std::cout << report.dump(2) << '\n';
        else if (ms2048 > 0 && ms4096 > 0)
            std::cout << "4096/2048 timestep ratio: " << std::fixed << std::setprecision(2) << ms4096 / ms2048 << "x\n";
        return ok ? 0 : 1;
    } catch (const std::exception& error) {
        std::cerr << "2dbench: " << error.what() << '\n';
        return 2;
    }
}
