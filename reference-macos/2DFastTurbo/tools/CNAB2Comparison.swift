import Foundation
import Metal

@main
private enum CNAB2Comparison {
    static func main() {
        do { try run() }
        catch {
            FileHandle.standardError.write(Data(("sim: \(error.localizedDescription)\n").utf8))
            exit(2)
        }
    }

    static func run() throws {
        var positional: [String] = [], output: String?, dump: String?
        let args = Array(CommandLine.arguments.dropFirst())
        var i = 0
        while i < args.count {
            let argument = args[i]
            if argument == "--help" || argument == "-h" {
                print("usage: sim [N Re K0 STEPS CFL metal UPDATE PAO|KM3 CNAB2] [--json FILE] [--state-out DIRECTORY]")
                print("Defaults: 512 10000 10 1001 0.25 metal 100 PAO CNAB2; SCIPYTURBO_SEED=1")
                print("Native Swift/Metal/VkFFT, including initialization. No Python is used.")
                return
            }
            if argument == "--json" || argument == "--state-out" {
                i += 1
                guard i < args.count else { throw FastTurboError.invalidConfiguration("Missing value for \(argument).") }
                if argument == "--json" { output = args[i] } else { dump = args[i] }
            } else if argument.hasPrefix("--") {
                throw FastTurboError.invalidConfiguration("Unknown option \(argument). Initialization is native; --python/--python-repo are no longer needed.")
            } else { positional.append(argument) }
            i += 1
        }
        guard positional.count <= 9 else { throw FastTurboError.invalidConfiguration("Too many positional arguments; use --help.") }
        var values = ["512", "10000", "10", "1001", "0.25", "metal", "100", "PAO", "CNAB2"]
        for (i, value) in positional.enumerated() { values[i] = value }
        guard let n = Int(values[0]), let re = Double(values[1]), let k0 = Double(values[2]),
              let steps = Int(values[3]), steps >= 0,
              let cfl = Float(values[4]), let update = Int(values[6]), update > 0,
              values[5].lowercased() == "metal", values[8].uppercased() == "CNAB2",
              let spectrum = StartSpectrum(rawValue: values[7].uppercased()),
              let seed = UInt64(ProcessInfo.processInfo.environment["SCIPYTURBO_SEED"] ?? "1"),
              re > 0, re.isFinite, k0 > 0, k0 <= 1_000_000, k0.isFinite else {
            throw FastTurboError.invalidConfiguration("Invalid arguments; use --help. Seed must be 1..5010.")
        }
        var c = FastTurboConfiguration()
        c.resolution = n; c.reynolds = re; c.initialWaveNumber = max(1, Int(k0)); c.seed = seed
        c.cfl = cfl; c.integrator = .cnab2; c.startSpectrum = spectrum
        try c.validateCNAB2()
        guard cfl > 0, cfl.isFinite else { throw FastTurboError.invalidConfiguration("CFL must be finite and positive.") }
        guard let device = MTLCreateSystemDefaultDevice(), let queue = device.makeCommandQueue() else { throw FastTurboError.noMetalDevice }
        let start = ProcessInfo.processInfo.systemUptime
        let solver = try FastTurboCNAB2(configuration: c, device: device, queue: queue, waveNumber: k0)
        let initializationSeconds = ProcessInfo.processInfo.systemUptime - start
        let initial = solver.row
        func save(_ name: String, _ buffer: MTLBuffer) throws {
            guard let dump else { return }
            let folder = URL(fileURLWithPath: dump, isDirectory: true)
            try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
            try Data(bytes: buffer.contents(), count: buffer.length).write(to: folder.appendingPathComponent(name))
        }
        try save("initial-omega.c64", solver.omega)
        print("Swift/Metal: \(device.name)\nOS: \(ProcessInfo.processInfo.operatingSystemVersionString)")
        print("--- RUN DNS ---")
        print(" N   = \(n)\n Re  = \(re)\n K0  = \(k0)\n Steps = \(steps)\n CFL  = \(cfl)\n Update = \(update)")
        print(" Start spectrum = \(spectrum.rawValue)\n Method = CNAB2\n requested = metal")
        print(" PAO seed in = \(seed)")
        print("FFT: VkFFT Metal R2C on \(device.name)")
        print(" Initializer = native historical PAO LCG\(spectrum == .km3 ? " + KM3 k^-3 shell redistribution" : "")")
        print(String(format: " Energy = %.9g  WiWi = %.9g  VISC = %.9g", solver.initialEnergy, solver.initialWiwi, solver.viscosity))
        print(String(format: " DNS INITIALIZATION took %.3f seconds", initializationSeconds))
        print(String(format: " Metal allocated memory ≈ %.2f MiB", Double(device.currentAllocatedSize) / 1048576))
        print(String(format: " [NEXTDT INIT] CFLM=%11.4f DT=%11.7f CN=%11.7f", initial.cflMaximum, initial.dt, initial.cn))
        print(String(format: " Initial DT=%11.7f CN=%11.7f", initial.dt, initial.cn))
        fflush(stdout)
        var rows: [[String: Any]] = []
        let runStart = ProcessInfo.processInfo.systemUptime
        try solver.advance(steps: steps, update: update, finalCheckpoint: true) { row in
            let tau = solver.tauL()
            guard tau.isFinite else { throw FastTurboError.nonFiniteState }
            print(String(format: " ITERATION %6llu T=%12.10f DT=%10.8f CN=%10.8f CFLM=%.6f TAU_L=%.6f",
                         row.iteration, row.time, row.dt, row.cn, row.cflMaximum, tau))
            rows.append(["iteration": row.iteration, "T": row.time, "state_time": row.stateTime,
                         "DT": row.dt, "CN": row.cn, "CFLM": row.cflMaximum, "TAU_L": tau])
        }
        let seconds = ProcessInfo.processInfo.systemUptime - runStart, row = solver.row
        print(String(format: " Elapsed CPU time for %d steps (s) = %.8g", steps, seconds))
        print(String(format: " Final T=%.8g  CN=%.8g  DT=%.8g", row.stateTime, row.cn, row.dt))
        print(String(format: " FPS = %.8g", Double(steps) / max(seconds, 1e-12)))
        let report: [String: Any] = [
            "schema": 1, "solver": "Native Swift/Metal CNAB2", "fft": "VkFFT Metal R2C",
            "initializer": "native", "gpu": device.name,
            "config": ["N": n, "Re": re, "K0": k0, "steps": steps, "CFL": cfl, "update": update,
                       "spectrum": spectrum.rawValue, "method": "CNAB2", "seed": seed],
            "initial": ["CFLM": initial.cflMaximum, "DT": initial.dt, "CN": initial.cn,
                        "Energy": solver.initialEnergy, "WiWi": solver.initialWiwi, "seed_out": solver.seedOut],
            "iterations": rows, "final": ["T": row.stateTime, "DT": row.dt, "CN": row.cn],
            "wall_seconds": seconds, "T_R": row.stateTime / max(seconds, 1e-12),
            "steps_per_second": Double(steps) / max(seconds, 1e-12), "initialization_seconds": initializationSeconds]
        if let output {
            let path = URL(fileURLWithPath: output)
            try FileManager.default.createDirectory(at: path.deletingLastPathComponent(), withIntermediateDirectories: true)
            try JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted, .sortedKeys]).write(to: path)
        }
        try save("omega.c64", solver.omega); try save("history.c64", solver.history); try save("velocity.f32", solver.velocity)
    }
}
