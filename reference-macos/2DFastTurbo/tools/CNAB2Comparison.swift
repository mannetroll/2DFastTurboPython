import AppKit
import CryptoKit
import Foundation
import Metal

private struct ComparisonError: LocalizedError {
    let message: String
    var errorDescription: String? { message }
}

private struct Params {
    var n: UInt32, m: UInt32, pitch: UInt32, groups: UInt32 = 64
    var argScale: Float = 0, c2: Float = 0, c3: Float = 0, divxz: Float
}

private final class CNAB2Solver {
    let device: MTLDevice
    let queue: MTLCommandQueue
    let fft: MTTVkFFTBackend
    let omega: MTLBuffer, history: MTLBuffer, padded: MTLBuffer, velocity: MTLBuffer
    let initialVelocity: MTLBuffer, partials: MTLBuffer
    let pipelines: [String: MTLComputePipelineState]
    let n: Int, m: Int, pitch: Int
    var params: Params

    init(n: Int, fixture: URL, metadata: [String: Any], shader: URL) throws {
        guard let device = MTLCreateSystemDefaultDevice(), let queue = device.makeCommandQueue() else {
            throw ComparisonError(message: "No Metal device is available.")
        }
        self.device = device; self.queue = queue; self.n = n
        m = 3 * n / 2; pitch = m / 2 + 1
        params = Params(n: UInt32(n), m: UInt32(m), pitch: UInt32(pitch),
                        divxz: 1 / (Float(m) * Float(m)))
        let options = MTLCompileOptions()
        options.mathMode = .safe
        let library = try device.makeLibrary(source: String(contentsOf: shader, encoding: .utf8), options: options)
        var pipelines: [String: MTLComputePipelineState] = [:]
        for name in ["prepareInitial", "products", "advanceCNAB2", "prepareInverse", "extractVelocity", "maximumVelocity"] {
            guard let function = library.makeFunction(name: name) else {
                throw ComparisonError(message: "Missing Metal kernel: \(name)")
            }
            pipelines[name] = try device.makeComputePipelineState(function: function)
        }
        self.pipelines = pipelines
        // Use the app's existing VkFFT bridge, without discarding any forward
        // coefficients. This runner retains Python's three nonlinear fields.
        fft = MTTVkFFTBackend(device: device, commandQueue: queue, resolution: UInt(m), realToComplex: true)
        guard fft.isAvailable else { throw ComparisonError(message: fft.lastErrorMessage ?? "VkFFT unavailable") }
        func allocate(_ length: Int) throws -> MTLBuffer {
            guard let buffer = device.makeBuffer(length: length, options: .storageModeShared) else {
                throw ComparisonError(message: "Cannot allocate \(length) Metal bytes")
            }
            memset(buffer.contents(), 0, length)
            return buffer
        }
        let spectralBytes = n * (n / 2) * 8
        omega = try allocate(spectralBytes); history = try allocate(spectralBytes)
        initialVelocity = try allocate(2 * spectralBytes)
        padded = try allocate(3 * m * pitch * 8)
        velocity = try allocate(2 * m * m * 4)
        partials = try allocate(64 * 4)
        let hashes = metadata["array_sha256"] as? [String: String] ?? [:]
        for (name, buffer) in [("omega", omega), ("velocity", initialVelocity)] {
            let data = try Data(contentsOf: fixture.appendingPathComponent(name + ".c64"))
            let hash = SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
            guard data.count == buffer.length, hash == hashes[name] else {
                throw ComparisonError(message: "Fixture length/hash mismatch: \(name)")
            }
            _ = data.withUnsafeBytes { memcpy(buffer.contents(), $0.baseAddress!, data.count) }
        }
        // Instantiate the forward three-field plan before timing. The inverse
        // two-field plan is instantiated while constructing initial velocity.
        let command = try makeCommand()
        try transform(command, batches: 3, inverse: false)
        try encode("prepareInitial", [initialVelocity, padded], command, width: pitch, height: m)
        try transform(command, batches: 2, inverse: true)
        try encode("extractVelocity", [padded, velocity], command, width: m, height: m)
        try finish(command)
    }

    func makeCommand() throws -> MTLCommandBuffer {
        guard let command = queue.makeCommandBuffer() else { throw ComparisonError(message: "Cannot create command buffer") }
        return command
    }

    func finish(_ command: MTLCommandBuffer) throws {
        command.commit(); command.waitUntilCompleted()
        if command.status == .error { throw command.error ?? ComparisonError(message: "Metal execution failed") }
    }

    func encode(_ name: String, _ buffers: [MTLBuffer], _ command: MTLCommandBuffer,
                width: Int, height: Int) throws {
        guard let encoder = command.makeComputeCommandEncoder(), let pipeline = pipelines[name] else {
            throw ComparisonError(message: "Cannot encode \(name)")
        }
        encoder.setComputePipelineState(pipeline)
        for (index, buffer) in buffers.enumerated() { encoder.setBuffer(buffer, offset: 0, index: index) }
        encoder.setBytes(&params, length: MemoryLayout<Params>.stride, index: buffers.count)
        encoder.dispatchThreads(MTLSize(width: width, height: height, depth: 1),
                                threadsPerThreadgroup: MTLSize(width: 16, height: 16, depth: 1))
        encoder.endEncoding()
    }

    func transform(_ command: MTLCommandBuffer, batches: Int, inverse: Bool) throws {
        guard fft.encode(buffer: padded, byteOffset: 0, batchCount: UInt(batches), inverse: inverse, commandBuffer: command) else {
            throw ComparisonError(message: fft.lastErrorMessage ?? "VkFFT encoding failed")
        }
    }

    func step(_ command: MTLCommandBuffer, dt: Double, cnm1: Double, viscosity: Float) throws {
        // Round each host scalar operation to Float32 exactly as _dns_step3_mlx.
        let d = Float(dt), previousCN = Float(cnm1)
        params.argScale = (Float(0.5) * viscosity) * d
        params.c2 = (Float(0.5) * d) * (Float(2) + previousCN)
        params.c3 = (-Float(0.5) * d) * previousCN
        try encode("products", [velocity, padded], command, width: m, height: m)
        try transform(command, batches: 3, inverse: false)
        try encode("advanceCNAB2", [padded, omega, history], command, width: n / 2, height: n)
        try encode("prepareInverse", [omega, padded], command, width: pitch, height: m)
        try transform(command, batches: 2, inverse: true)
        try encode("extractVelocity", [padded, velocity], command, width: m, height: m)
    }

    func cflMaximum() throws -> Double {
        let command = try makeCommand()
        guard let encoder = command.makeComputeCommandEncoder() else { throw ComparisonError(message: "Cannot reduce CFL") }
        encoder.setComputePipelineState(pipelines["maximumVelocity"]!)
        encoder.setBuffer(velocity, offset: 0, index: 0)
        encoder.setBuffer(partials, offset: 0, index: 1)
        encoder.setBytes(&params, length: MemoryLayout<Params>.stride, index: 2)
        encoder.dispatchThreads(MTLSize(width: 64 * 256, height: 1, depth: 1),
                                threadsPerThreadgroup: MTLSize(width: 256, height: 1, depth: 1))
        encoder.endEncoding()
        try finish(command)
        let values = partials.contents().bindMemory(to: Float.self, capacity: 64)
        var peak: Float = 0
        for i in 0..<64 { peak = max(peak, values[i]) }
        // Current Python MLX scales the maximum in Float32 before host transfer.
        let maximum = Double(peak * Float(Double(n) / (2 * Double.pi)))
        guard maximum.isFinite, maximum > 0 else { throw ComparisonError(message: "Nonfinite or zero CFL maximum") }
        return maximum
    }

    func tauL() throws -> Double {
        let values = omega.contents().bindMemory(to: SIMD2<Float>.self, capacity: n * (n / 2))
        var energy = 0.0, length = 0.0
        for z in 0..<n {
            let y = Float(z < n / 2 ? z : z - n)
            for x in 0..<(n / 2) {
                let k2 = Float(x * x) + y * y
                if k2 <= 0 { continue }
                let om = values[z * (n / 2) + x]
                var power: Float = om.x * om.x + om.y * om.y
                power = power / k2
                // Match _flow_eddy_metrics_mlx_impl, including its endpoint weight.
                let weight: Float = x == 0 || x == n / 2 - 1 ? 1 : 2
                let weighted: Float = power * weight
                let inverseK: Float = 1 / sqrt(k2)
                energy += Double(weighted)
                length += Double(weighted * inverseK)
            }
        }
        let value = (2 * Double.pi * length / energy) / sqrt(energy)
        guard value.isFinite else { throw ComparisonError(message: "Nonfinite TAU_L") }
        return value
    }

    func dump(_ path: URL) throws {
        try FileManager.default.createDirectory(at: path, withIntermediateDirectories: true)
        for (name, buffer) in [("omega.c64", omega), ("history.c64", history), ("velocity.f32", velocity)] {
            try Data(bytes: buffer.contents(), count: buffer.length).write(to: path.appendingPathComponent(name))
        }
    }
}

@main
private enum CNAB2Comparison {
    static func main() {
        do { try run() }
        catch {
            FileHandle.standardError.write(Data(("Swift CNAB2: \(error.localizedDescription)\n").utf8))
            exit(2)
        }
    }

    static func run() throws {
        let args = Array(CommandLine.arguments.dropFirst())
        guard args.count == 6, let steps = Int(args[1]), steps >= 0,
              let update = Int(args[2]), update > 0 else {
            throw ComparisonError(message: "Use tools/sim to invoke the comparison runner.")
        }
        let fixture = URL(fileURLWithPath: args[0])
        guard let meta = try JSONSerialization.jsonObject(with: Data(contentsOf: fixture.appendingPathComponent("fixture.json"))) as? [String: Any],
              meta["schema"] as? Int == 1,
              let n = meta["n"] as? Int, n >= 16, n <= 2048, n & (n - 1) == 0,
              let re = meta["re"] as? Double, let k0 = meta["k0"] as? Double,
              let cfl = meta["cfl"] as? Double, let seed = meta["seed"] as? Int,
              let visc = meta["viscosity"] as? Double, (1...5010).contains(seed),
              [re, k0, cfl, visc].allSatisfy({ $0.isFinite && $0 > 0 }), Float(visc).isFinite else {
            throw ComparisonError(message: "Invalid fixture configuration")
        }
        _ = NSApplication.shared
        let startInit = ProcessInfo.processInfo.systemUptime
        let solver = try CNAB2Solver(n: n, fixture: fixture, metadata: meta, shader: URL(fileURLWithPath: args[3]))
        let cfl0 = try solver.cflMaximum()
        let initSeconds = ProcessInfo.processInfo.systemUptime - startInit
        print("Swift/Metal: \(solver.device.name)")
        print("OS: \(ProcessInfo.processInfo.operatingSystemVersionString)")
        print("\n$ SCIPYTURBO_SEED=\(seed) tools/sim \(n) \(re) \(k0) \(steps) \(cfl) metal \(update) PAO CNAB2")
        print("--- RUN DNS ---")
        print(" N   = \(n)\n Re  = \(re)\n K0  = \(k0)\n Steps = \(steps)\n CFL  = \(cfl)\n Update = \(update)")
        print(" Start spectrum = PAO\n Method = CNAB2\n requested = metal")
        print("FFT: VkFFT Metal R2C on \(solver.device.name)")
        let date = DateFormatter(); date.dateFormat = "yyyy-MM-dd HH:mm"
        print("--- INITIALIZING Swift/Metal --- \(date.string(from: Date()))")
        print(" N=\(n), K0=\(k0), Re=\(re)")
        print(" Start spec. = PAO k*exp(-(k/K0)^2)")
        print(" PAO initializer = shared Python spectrum (no Python timesteps)")
        for line in meta["diagnostics"] as? [String] ?? [] { print(line) }
        print(" effective = metal (Swift CNAB2 comparison runner)")
        print(String(format: " DNS INITIALIZATION took %.3f seconds", initSeconds))
        print(String(format: " Metal allocated memory ≈ %.2f MiB", Double(solver.device.currentAllocatedSize) / 1048576))
        var t = 0.0, dt = cfl / (cfl0 * Double.pi), cn = 1.0, cnm1 = 0.0
        print(String(format: " [NEXTDT INIT] CFLM=%11.4f DT=%11.7f CN=%11.7f", cfl0, dt, cn))
        print(String(format: " Initial DT=%11.7f CN=%11.7f", dt, cn))
        let initialDT = dt
        var rows: [[String: Any]] = []
        let start = ProcessInfo.processInfo.systemUptime
        var command = try solver.makeCommand()
        var queued = 0
        if steps > 0 {
            for iteration in 1...steps {
                let dtOld = dt
                try autoreleasepool { try solver.step(command, dt: dt, cnm1: cnm1, viscosity: Float(visc)) }
                queued += 1
                cnm1 = cn
                let checkpoint = iteration == 1 || iteration % update == 0 || iteration == steps
                if checkpoint || queued == 128 {
                    try solver.finish(command)
                    queued = 0
                    if checkpoint {
                        let maximum = try solver.cflMaximum()
                        cn = 0.8 + 0.2 * (cfl / (maximum * dt * Double.pi))
                        dt = dt * cn
                        let tau = try solver.tauL()
                        print(String(format: " ITERATION %6d T=%12.10f DT=%10.8f CN=%10.8f CFLM=%.6f TAU_L=%.6f",
                                     iteration, t, dt, cn, maximum, tau))
                        rows.append(["iteration": iteration, "T": t, "state_time": t + dtOld,
                                     "DT": dt, "CN": cn, "CFLM": maximum, "TAU_L": tau])
                    }
                    if iteration != steps { command = try solver.makeCommand() }
                }
                // Python prints pre-increment T but diagnostics of the completed step.
                t += dtOld
            }
        }
        let seconds = ProcessInfo.processInfo.systemUptime - start
        print(String(format: " Elapsed CPU time for %d steps (s) = %.8g", steps, seconds))
        print(String(format: " Final T=%.8g  CN=%.8g  DT=%.8g", t, cn, dt))
        print(String(format: " FPS = %.8g", Double(steps) / max(seconds, 1e-12)))
        let result: [String: Any] = [
            "schema": 1, "solver": "Swift/Metal CNAB2 comparison runner", "fft": "VkFFT Metal R2C",
            "config": ["N": n, "Re": re, "K0": k0, "steps": steps, "CFL": cfl, "update": update,
                       "spectrum": "PAO", "method": "CNAB2", "seed": seed],
            "fixture": meta, "initial": ["CFLM": cfl0, "DT": initialDT, "CN": 1.0],
            "iterations": rows, "final": ["T": t, "DT": dt, "CN": cn, "CNM1": cnm1],
            "wall_seconds": seconds, "T_R": t / max(seconds, 1e-12),
            "steps_per_second": Double(steps) / max(seconds, 1e-12),
            "initialization_seconds": initSeconds, "gpu": solver.device.name,
            "note": "Dedicated CNAB2 comparison mode; not the optimized app's ETDRK4/RK3 throughput. FFT roundoff may differ from MLX."
        ]
        if !args[4].isEmpty {
            try JSONSerialization.data(withJSONObject: result, options: [.prettyPrinted, .sortedKeys])
                .write(to: URL(fileURLWithPath: args[4]))
        }
        if !args[5].isEmpty { try solver.dump(URL(fileURLWithPath: args[5])) }
    }
}
