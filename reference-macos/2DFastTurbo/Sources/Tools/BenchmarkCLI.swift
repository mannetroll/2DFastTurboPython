import AppKit
import CryptoKit
import Foundation
import Metal

enum BenchmarkCLI {
    private static var defaults: FastTurboConfiguration {
        #if LEGACY_METAL_REF
        // The captured baseline must stay pinned when the new app's measured
        // default changes. Explicit command-line overrides still apply below.
        var configuration = FastTurboConfiguration()
        configuration.integrator = .imexRK3
        configuration.nonlinearFormulation = .stress
        configuration.cfl = 1.5
        return configuration
        #else
        return FastTurboConfiguration()
        #endif
    }

    struct Failure: LocalizedError {
        let message: String
        var errorDescription: String? { message }
    }

    static let help = """
    2DFastTurbo --benchmark [options]
      --resolution N       Retained grid; nonlinear grid is 3N/2 (default 1024)
      --reynolds R         Viscosity is 1/R (default 50000)
      --k0 K --seed S       Pao initial spectrum (defaults 4, 2)
      --integrator METHOD  imexRK3, ifHeunRK3, or etdrk4 (default \(defaults.integrator.rawValue))
      --operator FORM      stress or advective (default \(defaults.nonlinearFormulation.rawValue))
      --cfl C              GPU Courant controller (default \(defaults.cfl))
      --fixed-dt DT        Fixed timestep for independent numerical validation
      --steps K            Number of measured steps (default 200)
      --until T            Stop exactly at physical time T, shortening the last step
      --turnovers K        Alternative endpoint in initial turnover times
      --wall-seconds S     Measure for S wall seconds, completing the final GPU batch
      --batch K            Steps per command buffer, 1...32 (default 8)
      --warmup K           Untimed steps before resetting the initial state (default 8)
      --initial FILE       Little-endian Float32 canonical vorticity [y,x]
      --spectrum FILE      Write normalized complex64 Fourier coefficients [y,x]
      --fields FILE        Write physical Float32 [omega,u,v] planes at the endpoint
      --output FILE        Save result JSON as well as printing it
      --snapshot-hz HZ     Include display-field preparation (no presentation)
      --help               Show this help

    The app also accepts --gui-benchmark and --live-check for visible-app checks.
    """

    static func run() -> Int32 {
        do {
            try execute()
            return 0
        } catch {
            FileHandle.standardError.write(Data(("2DFastTurbo: " + error.localizedDescription + "\n").utf8))
            return 2
        }
    }

    private static func execute() throws {
        let arguments = Array(CommandLine.arguments.dropFirst())
        if arguments.contains("--help") {
            print(help)
            return
        }
        let switches: Set<String> = ["--benchmark"]
        let values: Set<String> = ["--resolution", "--reynolds", "--k0", "--seed", "--cfl", "--integrator", "--operator", "--fixed-dt", "--steps", "--until", "--turnovers", "--wall-seconds", "--batch", "--warmup", "--initial", "--spectrum", "--fields", "--output", "--snapshot-hz"]
        var options: [String: String] = [:]
        var i = 0
        while i < arguments.count {
            let key = arguments[i]
            if switches.contains(key) { i += 1; continue }
            guard values.contains(key), i + 1 < arguments.count else {
                throw Failure(message: "Unknown or incomplete option: \(key). Use --help.")
            }
            guard options[key] == nil else { throw Failure(message: "Repeated option: \(key)") }
            options[key] = arguments[i + 1]
            i += 2
        }
        func integer(_ key: String, _ fallback: Int) throws -> Int {
            guard let value = options[key] else { return fallback }
            guard let result = Int(value) else { throw Failure(message: "\(key) must be an integer.") }
            return result
        }
        func number(_ key: String, _ fallback: Double) throws -> Double {
            guard let value = options[key] else { return fallback }
            guard let result = Double(value), result.isFinite else { throw Failure(message: "\(key) must be finite.") }
            return result
        }
        var configuration = defaults
        configuration.resolution = try integer("--resolution", configuration.resolution)
        configuration.reynolds = try number("--reynolds", configuration.reynolds)
        configuration.initialWaveNumber = try integer("--k0", configuration.initialWaveNumber)
        configuration.cfl = Float(try number("--cfl", Double(configuration.cfl)))
        if let seed = options["--seed"] {
            guard let value = UInt64(seed) else { throw Failure(message: "--seed must be an unsigned 64-bit integer.") }
            configuration.seed = value
        }
        if let method = options["--integrator"] {
            guard let integrator = Integrator(rawValue: method) else { throw Failure(message: "Unknown integrator: \(method)") }
            configuration.integrator = integrator
        }
        if let name = options["--operator"] {
            guard let formulation = NonlinearFormulation(rawValue: name) else { throw Failure(message: "Unknown operator: \(name)") }
            configuration.nonlinearFormulation = formulation
        }
        if options["--fixed-dt"] != nil {
            configuration.fixedTimeStep = Float(try number("--fixed-dt", 0))
            guard configuration.fixedTimeStep! > 0, configuration.fixedTimeStep!.isFinite else { throw Failure(message: "--fixed-dt must be positive.") }
        }
        let batch = try integer("--batch", 8)
        let steps = try integer("--steps", 200)
        let warmup = try integer("--warmup", 8)
        let snapshotHz = try number("--snapshot-hz", 0)
        guard (1...32).contains(batch), steps >= 0, (0...4096).contains(warmup), snapshotHz >= 0, snapshotHz <= 240 else {
            throw Failure(message: "Expected batch 1...32, nonnegative steps, warmup 0...4096, snapshot-hz 0...240.")
        }
        let endpointOptions = ["--steps", "--until", "--turnovers", "--wall-seconds"].filter { options[$0] != nil }
        guard endpointOptions.count <= 1 else { throw Failure(message: "Choose one of --steps, --until, --turnovers, or --wall-seconds.") }
        let wallSeconds: Double?
        if options["--wall-seconds"] != nil {
            let requested = try number("--wall-seconds", 0)
            guard requested > 0 else { throw Failure(message: "--wall-seconds must be positive.") }
            wallSeconds = requested
        } else {
            wallSeconds = nil
        }
        guard configuration.resolution >= 16, configuration.resolution <= 16384,
              configuration.resolution % 2 == 0, configuration.reynolds > 0,
              configuration.initialWaveNumber > 0, configuration.cfl > 0, configuration.cfl.isFinite else {
            throw Failure(message: "Invalid grid, Reynolds number, spectrum scale, or CFL.")
        }

        // VkFFT's Metal objects participate in the AppKit autorelease runtime.
        _ = NSApplication.shared
        guard let device = MTLCreateSystemDefaultDevice() else { throw Failure(message: "No Metal device is available to this process.") }
        var initialField: [Float]?
        var initialHash: String?
        if let path = options["--initial"] {
            let data = try Data(contentsOf: URL(fileURLWithPath: path))
            initialHash = SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
            let expected = configuration.resolution * configuration.resolution
            guard data.count == expected * MemoryLayout<Float>.size else {
                throw Failure(message: "Initial field must contain exactly \(expected) little-endian Float32 values.")
            }
            initialField = data.withUnsafeBytes { bytes in
                (0..<expected).map { Float(bitPattern: UInt32(littleEndian: bytes.loadUnaligned(fromByteOffset: $0 * 4, as: UInt32.self))) }
            }
            guard initialField!.allSatisfy(\.isFinite) else { throw Failure(message: "Initial field contains a nonfinite value.") }
        }
        let core = try FastTurboCore(configuration: configuration, device: device, vorticity: initialField)
        if warmup > 0 {
            _ = try core.advanceSync(steps: warmup)
            if snapshotHz > 0 || options["--fields"] != nil { _ = try core.snapshotSync(includeVelocity: true) }
            try core.resetSync(vorticity: initialField)
        }
        let initial = try core.inspectSync()
        var endpoint: Double?
        if options["--until"] != nil { endpoint = try number("--until", 0) }
        if options["--turnovers"] != nil { endpoint = try number("--turnovers", 0) * initial.initialTurnoverTime }
        if let endpoint, endpoint < 0 { throw Failure(message: "The physical endpoint must be nonnegative.") }
        var statistics = initial
        var batches = 0
        var snapshots = 0
        var nextSnapshot = 0.0
        var gpuSeconds = 0.0
        var finalBatchSeconds = 0.0
        let start = ProcessInfo.processInfo.systemUptime
        // A compensated pair of Float values can differ from the caller's Double
        // endpoint by its final rounding bit. Do not encode a phantom extra step.
        func shouldContinue() -> Bool {
            if let wallSeconds { return ProcessInfo.processInfo.systemUptime - start < wallSeconds }
            return endpoint.map({ statistics.time < $0 - 32 * max(abs($0), Double.leastNormalMagnitude) * Double.ulpOfOne })
                ?? (statistics.step < initial.step + UInt64(steps))
        }
        while shouldContinue() {
            let batchStart = ProcessInfo.processInfo.systemUptime
            let count = endpoint == nil && wallSeconds == nil ? min(batch, steps - Int(statistics.step - initial.step)) : batch
            let previous = statistics
            statistics = try autoreleasepool { try core.advanceSync(steps: count, until: endpoint) }
            batches += 1
            gpuSeconds += statistics.gpuMilliseconds / 1000
            guard statistics.isFinite, statistics.step > previous.step, statistics.time > previous.time else {
                throw Failure(message: "The solver stopped making finite physical progress at t=\(statistics.time).")
            }
            let elapsed = ProcessInfo.processInfo.systemUptime - start
            if snapshotHz > 0, elapsed >= nextSnapshot {
                _ = try core.snapshotSync(includeVelocity: true)
                snapshots += 1
                nextSnapshot = elapsed + 1 / snapshotHz
            }
            finalBatchSeconds = ProcessInfo.processInfo.systemUptime - batchStart
        }
        let finalInspectionStart = ProcessInfo.processInfo.systemUptime
        statistics = try core.inspectSync()
        let completedAt = ProcessInfo.processInfo.systemUptime
        let seconds = completedAt - start
        let finalInspectionSeconds = completedAt - finalInspectionStart
        let advanced = statistics.time - initial.time
        let completed = statistics.step - initial.step
        if let path = options["--spectrum"] {
            let values = try core.spectrumSync()
            let data = values.withUnsafeBytes { Data($0) }
            try data.write(to: URL(fileURLWithPath: path), options: .atomic)
        }
        if let path = options["--fields"] {
            guard let frame = try core.snapshotSync(includeVelocity: true) else { throw Failure(message: "No snapshot slot was available.") }
            let count = 3 * frame.gridSize * frame.gridSize * MemoryLayout<Float>.size
            try Data(bytes: frame.buffer.contents(), count: count).write(to: URL(fileURLWithPath: path), options: .atomic)
        }
        func stats(_ value: FastTurboStatistics) -> [String: Any] {
            ["steps": value.step, "simulation_time": value.time, "dt": value.timeStep,
             "energy": value.energy, "enstrophy": value.enstrophy,
             "initial_turnover_time": value.initialTurnoverTime, "is_finite": value.isFinite]
        }
        #if LEGACY_METAL_REF
        let solverName = "Metal2DTurbo (captured baseline adapter)"
        #else
        let solverName = "2DFastTurbo"
        #endif
        let result: [String: Any] = [
            "schema_version": 1, "solver": solverName, "mode": snapshotHz > 0 ? "headless-with-snapshots" : "headless",
            "config": ["resolution": configuration.resolution, "padded_resolution": configuration.paddedResolution,
                       "reynolds": configuration.reynolds, "viscosity": configuration.viscosity,
                       "k0": configuration.initialWaveNumber, "seed": configuration.seed,
                       "cfl": configuration.cfl, "integrator": configuration.integrator.rawValue,
                       "operator": configuration.nonlinearFormulation.rawValue,
                       "batch": batch, "fixed_dt": configuration.fixedTimeStep as Any? ?? NSNull()],
            "initial_stats": stats(initial), "stats": stats(statistics),
            "seconds": seconds, "simtime_per_s": advanced / max(seconds, 1e-12),
            "steps_per_s": Double(completed) / max(seconds, 1e-12),
            "turnovers_per_s": advanced / max(initial.initialTurnoverTime * seconds, 1e-12),
            "gpu_seconds": gpuSeconds, "batches": batches, "snapshots": snapshots,
            "snapshot_hz": snapshotHz, "warmup_steps": warmup,
            "target_wall_seconds": wallSeconds as Any? ?? NSNull(),
            "final_batch_seconds": finalBatchSeconds, "final_inspection_seconds": finalInspectionSeconds,
            "target_time": endpoint as Any? ?? NSNull(), "spectrum_normalization": "fourier-series",
            "initial_source": initialField == nil ? "seeded-pao" : "external-physical-field",
            "initial_file": options["--initial"] as Any? ?? NSNull(),
            "initial_sha256": initialHash as Any? ?? NSNull(),
            "vorticity_convention": "omega = dx(v) - dy(u)",
            "hardware": ["gpu": device.name, "registry_id": String(device.registryID),
                         "os": ProcessInfo.processInfo.operatingSystemVersionString,
                         "physical_memory_bytes": ProcessInfo.processInfo.physicalMemory,
                         "allocated_metal_bytes": device.currentAllocatedSize,
                         "owned_solver_buffer_bytes": statistics.allocatedBytes,
                         "thermal_state": ProcessInfo.processInfo.thermalState.rawValue,
                         "low_power_mode": ProcessInfo.processInfo.isLowPowerModeEnabled],
            "timestamp": ISO8601DateFormatter().string(from: Date()),
        ]
        let json = try JSONSerialization.data(withJSONObject: result, options: [.prettyPrinted, .sortedKeys])
        if let path = options["--output"] { try json.write(to: URL(fileURLWithPath: path), options: .atomic) }
        FileHandle.standardOutput.write(json)
        FileHandle.standardOutput.write(Data("\n".utf8))
    }
}
