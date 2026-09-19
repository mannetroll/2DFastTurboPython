import Foundation
import Metal

/// Historical compact, negative-curl CNAB2 state, shared by tools/sim and the app.
/// Initialization is native host work; every timestep and FFT stays on Metal/VkFFT.
final class FastTurboCNAB2 {
    struct Parameters {
        var n, m, pitch: UInt32
        var groups: UInt32 = 64
        var argScale: Float = 0, c2: Float = 0, c3: Float = 0, divxz: Float
        var dx: Float
    }
    struct Row {
        var iteration: UInt64 = 0
        var time = 0.0, stateTime = 0.0
        var dt: Float = 0, cn: Float = 1, cflMaximum: Float = 0
    }
    let device: MTLDevice
    let queue: MTLCommandQueue
    let n: Int, m: Int, pitch: Int
    let omega: MTLBuffer, history: MTLBuffer, velocity: MTLBuffer
    private let padded: MTLBuffer, partials: MTLBuffer
    private let fft: MTTVkFFTBackend
    private let pipelines: [String: MTLComputePipelineState]
    private var parameters: Parameters
    private let fixedDT: Float?
    var viscosity: Float
    var cfl: Float
    private(set) var row = Row()
    private var previousCN: Float = 0, resumeRatio: Float = 0
    private(set) var gpuMilliseconds = 0.0
    private(set) var initialEnergy = 0.0, initialWiwi = 0.0
    private(set) var seedOut = 0
    var allocatedBytes: Int { omega.length + history.length + velocity.length + padded.length + partials.length }

    init(configuration c: FastTurboConfiguration, device: MTLDevice, queue: MTLCommandQueue,
         waveNumber: Double? = nil) throws {
        try c.validateCNAB2()
        let k0 = waveNumber ?? Double(c.initialWaveNumber)
        guard c.cfl > 0, c.cfl.isFinite, k0 > 0, Float(k0).isFinite,
              c.fixedTimeStep.map({ $0 > 0 && $0.isFinite }) ?? true else {
            throw FastTurboError.invalidConfiguration("CNAB2 requires positive finite K0, CFL and optional fixed timestep.")
        }
        let n = c.resolution, m = 3 * n / 2, pitch = m / 2 + 1
        let options = MTLCompileOptions(); options.mathMode = .safe
        let library = try device.makeLibrary(source: FastTurboCNAB2Shaders.source, options: options)
        var states: [String: MTLComputePipelineState] = [:]
        for name in ["prepareInitial", "prepareInverse", "products", "advanceCNAB2", "extractVelocity", "maximumVelocity", "copySpectrum"] {
            guard let function = library.makeFunction(name: name) else { throw FastTurboError.metal("Missing CNAB2 kernel: \(name)") }
            states[name] = try device.makeComputePipelineState(function: function)
        }
        let fft = MTTVkFFTBackend(device: device, commandQueue: queue, resolution: UInt(m), realToComplex: true)
        guard fft.isAvailable else { throw FastTurboError.metal(fft.lastErrorMessage ?? "CNAB2 VkFFT unavailable.") }
        func allocate(_ count: Int) throws -> MTLBuffer {
            guard let buffer = device.makeBuffer(length: count, options: .storageModeShared) else {
                throw FastTurboError.metal("Cannot allocate CNAB2 buffer (\(count) bytes).")
            }
            memset(buffer.contents(), 0, count)
            return buffer
        }
        let bytes = n * (n / 2) * 8
        let omega = try allocate(bytes), history = try allocate(bytes)
        let padded = try allocate(3 * m * pitch * 8), velocity = try allocate(2 * m * m * 4)
        let partials = try allocate(64 * 4)
        let initial = try allocate(2 * bytes)
        self.device = device; self.queue = queue; self.n = n; self.m = m; self.pitch = pitch
        self.fft = fft; pipelines = states
        self.omega = omega; self.history = history; self.padded = padded
        self.velocity = velocity; self.partials = partials
        cfl = c.cfl; viscosity = c.viscosity; fixedDT = c.fixedTimeStep
        parameters = Parameters(n: UInt32(n), m: UInt32(m), pitch: UInt32(pitch),
                                divxz: 1 / (Float(m) * Float(m)), dx: 2 * Float(Double.pi) / Float(n))
        try initializeGPU(configuration: c, k0: k0, initial: initial)
    }

    @inline(never) private func initializeGPU(configuration c: FastTurboConfiguration, k0: Double, initial: MTLBuffer) throws {
        let metrics = Self.initialize(n: n, k0: Float(k0), seed: Int(c.seed), spectrum: c.startSpectrum,
                                      velocity: initial, omega: omega)
        initialEnergy = metrics.energy; initialWiwi = metrics.wiwi; seedOut = metrics.seed
        let command = try command()
        // Materialize the three-field forward plan before any timed step.
        try transform(command, batches: 3, inverse: false)
        try encode("prepareInitial", [initial, padded], command, width: pitch, height: m)
        try inverse(command)
        try finish(command)
        row.cflMaximum = try maximum()
        row.dt = fixedDT ?? (cfl / (row.cflMaximum > 0 ? row.cflMaximum : 1) / Float(Double.pi))
        gpuMilliseconds = 0
    }

    private func command() throws -> MTLCommandBuffer {
        guard let result = queue.makeCommandBuffer() else { throw FastTurboError.metal("Cannot create CNAB2 command buffer.") }
        return result
    }
    private func finish(_ command: MTLCommandBuffer) throws {
        command.commit(); command.waitUntilCompleted()
        if let error = command.error { throw error }
        guard command.status == .completed else { throw FastTurboError.metal("CNAB2 Metal command did not complete.") }
        gpuMilliseconds += max(0, command.gpuEndTime - command.gpuStartTime) * 1000
    }
    private func encode(_ name: String, _ buffers: [MTLBuffer], _ command: MTLCommandBuffer, width: Int, height: Int) throws {
        guard let encoder = command.makeComputeCommandEncoder(), let pipeline = pipelines[name] else {
            throw FastTurboError.metal("Cannot encode CNAB2 \(name).")
        }
        encoder.setComputePipelineState(pipeline)
        for (i, buffer) in buffers.enumerated() { encoder.setBuffer(buffer, offset: 0, index: i) }
        encoder.setBytes(&parameters, length: MemoryLayout<Parameters>.stride, index: buffers.count)
        let threads = name == "maximumVelocity" ? MTLSize(width: 256, height: 1, depth: 1) : MTLSize(width: 16, height: 16, depth: 1)
        encoder.dispatchThreads(MTLSize(width: width, height: height, depth: 1), threadsPerThreadgroup: threads)
        encoder.endEncoding()
    }
    private func transform(_ command: MTLCommandBuffer, batches: Int, inverse: Bool) throws {
        guard fft.encode(buffer: padded, byteOffset: 0, batchCount: UInt(batches), inverse: inverse, commandBuffer: command) else {
            throw FastTurboError.metal(fft.lastErrorMessage ?? "CNAB2 VkFFT encoding failed.")
        }
    }
    private func inverse(_ command: MTLCommandBuffer) throws {
        try transform(command, batches: 2, inverse: true)
        try encode("extractVelocity", [padded, velocity], command, width: m, height: m)
    }
    private func maximum() throws -> Float {
        let command = try command()
        try encode("maximumVelocity", [velocity, partials], command, width: 64 * 256, height: 1)
        try finish(command)
        let p = partials.contents().bindMemory(to: Float.self, capacity: 64)
        let value = (0..<64).reduce(Float(0)) { max($0, p[$1]) }
        guard value >= 0, value.isFinite else { throw FastTurboError.nonFiniteState }
        return value
    }
    private func step(_ command: MTLCommandBuffer, dt: Float) throws {
        parameters.argScale = (0.5 * viscosity) * dt
        parameters.c2 = (0.5 * dt) * (2 + previousCN)
        parameters.c3 = (-0.5 * dt) * previousCN
        try encode("products", [velocity, padded], command, width: m, height: m)
        try transform(command, batches: 3, inverse: false)
        try encode("advanceCNAB2", [padded, omega, history], command, width: n / 2, height: n)
        try encode("prepareInverse", [omega, padded], command, width: pitch, height: m)
        try inverse(command)
    }

    /// Batch boundaries and snapshots never introduce controller checkpoints.
    func advance(steps: Int, until: Double? = nil, update: Int = 100, finalCheckpoint: Bool = false,
                 checkpoint: ((Row) throws -> Void)? = nil) throws {
        guard steps >= 0, update > 0, UInt64(steps) <= UInt64.max - row.iteration,
              until.map({ $0 >= 0 && $0.isFinite }) ?? true else {
            throw FastTurboError.invalidConfiguration("Invalid CNAB2 step count, update interval or endpoint.")
        }
        gpuMilliseconds = 0
        let target = row.iteration + UInt64(steps)
        var pending: MTLCommandBuffer?
        while row.iteration < target {
            if let until, row.stateTime >= until - max(1, until) * 1e-14 { break }
            if resumeRatio > 0 { previousCN = resumeRatio; resumeRatio = 0 }
            var dt = row.dt
            let clipped = until.map { Double(dt) >= $0 - row.stateTime } ?? false
            if clipped {
                dt = Float(until! - row.stateTime)
                previousCN *= dt / row.dt
            }
            guard dt > 0, dt.isFinite else { throw FastTurboError.nonFiniteState }
            if pending == nil { pending = try command() }
            try autoreleasepool { try step(pending!, dt: dt) }
            previousCN = row.cn
            row.time = row.stateTime
            row.iteration += 1
            row.stateTime = clipped ? until! : row.stateTime + Double(dt)
            let sample = row.iteration == 1 || row.iteration % UInt64(update) == 0 || (finalCheckpoint && row.iteration == target)
            if sample || row.iteration % 128 == 0 || row.iteration == target || clipped {
                try finish(pending!); pending = nil
            }
            if sample {
                row.cflMaximum = try maximum()
                let currentCFL = (row.cflMaximum * row.dt) * Float(Double.pi)
                row.cn = fixedDT != nil || currentCFL == 0 ? 1 : 0.8 + 0.2 * (cfl / currentCFL)
                row.dt = fixedDT ?? (row.dt * row.cn)
                try checkpoint?(row)
            }
            if clipped { resumeRatio = row.dt / dt }
        }
        if let pending { try finish(pending) }
    }

    func copySpectrum(_ canonical: MTLBuffer, importing: Bool = false) throws {
        let command = try command()
        guard let encoder = command.makeComputeCommandEncoder() else { throw FastTurboError.metal("Cannot copy CNAB2 spectrum.") }
        encoder.setComputePipelineState(pipelines["copySpectrum"]!)
        encoder.setBuffer(importing ? canonical : omega, offset: 0, index: 0)
        encoder.setBuffer(importing ? omega : canonical, offset: 0, index: 1)
        var size = UInt32(n), direction = UInt32(importing ? 1 : 0)
        var scale = importing ? -1 / Float(n * n) : -Float(n * n)
        encoder.setBytes(&size, length: 4, index: 2)
        encoder.setBytes(&scale, length: 4, index: 3)
        encoder.setBytes(&direction, length: 4, index: 4)
        encoder.dispatchThreads(MTLSize(width: n / 2 + 1, height: n, depth: 1), threadsPerThreadgroup: MTLSize(width: 16, height: 16, depth: 1))
        encoder.endEncoding()
        try finish(command)
        if importing {
            memset(history.contents(), 0, history.length)
            let command = try self.command()
            try encode("prepareInverse", [omega, padded], command, width: pitch, height: m)
            try inverse(command); try finish(command)
            row = Row(); previousCN = 0; resumeRatio = 0
            row.cflMaximum = try maximum()
            row.dt = fixedDT ?? (cfl / (row.cflMaximum > 0 ? row.cflMaximum : 1) / Float(Double.pi))
        }
    }

    func tauL() -> Double {
        let p = omega.contents().bindMemory(to: SIMD2<Float>.self, capacity: n * (n / 2))
        var energy = 0.0, length = 0.0
        for z in 0..<n { for x in 0..<(n / 2) {
            let y = Float(z < n / 2 ? z : z - n), k2 = Float(x * x) + y * y
            if k2 == 0 { continue }
            let w = p[z * (n / 2) + x]
            let power = (w.x * w.x + w.y * w.y) / k2
            let weighted = power * (x == 0 || x == n / 2 - 1 ? Float(1) : Float(2))
            energy += Double(weighted); length += Double(weighted * (1 / sqrt(k2)))
        } }
        return energy > 0 ? (2 * Double.pi * length / energy) / sqrt(energy) : 0
    }

    private static func initialize(n: Int, k0: Float, seed: Int, spectrum: StartSpectrum,
                                   velocity: MTLBuffer, omega: MTLBuffer) -> (energy: Double, wiwi: Double, seed: Int) {
        let half = n / 2, count = n * half, norm = Float(Double.pi) * k0 * k0
        let u = velocity.contents().bindMemory(to: SIMD2<Float>.self, capacity: 2 * count)
        let w = omega.contents().bindMemory(to: SIMD2<Float>.self, capacity: count)
        var state = seed
        func next() -> Float { state = (state * 420029 + 2017) % 5011; return Float(state) / 5011 }
        for _ in 0..<97 { _ = next() }
        var random = (0..<97).map { _ in next() }
        for z in 0..<n { for x in 0..<half {
            let y = Float(z < half ? z : z - n), ax = Float(x), r = next()
            let index = min(96, max(0, Int(Double(r) * 97)))
            let phase = (2 * Float(Double.pi)) * random[index]; random[index] = r
            let k = sqrt(ax * ax + y * y), ratio = k / k0
            let power = exp(-ratio * ratio)
            let amplitude = sqrt(power / (x == 0 ? norm : (1 + (y * y) / (ax * ax)) * norm))
            let direction = SIMD2(amplitude * cos(phase), amplitude * sin(phase)), i = z * half + x
            if x == 0 { u[i] = direction }
            else { u[count + i] = direction; u[i] = -(y / ax) * direction }
        } }
        u[0] = .zero; u[count] = .zero
        for component in 0..<2 {
            for z in 1..<half { let value = u[component * count + z * half]; u[component * count + (n - z) * half] = SIMD2(value.x, -value.y) }
            for x in 0..<half { u[component * count + half * half + x] = .zero }
        }
        if spectrum == .km3 { reshapeKM3(n: n, k0: Double(k0), seed: seed, velocity: u) }
        var energy = 0.0, wiwi = 0.0
        for x in 0..<half { for z in 0..<n {
            let i = z * half + x, a = u[i], b = u[count + i], y = z < half ? z : z - n
            let power = Double(a.x) * Double(a.x) + Double(a.y) * Double(a.y) + Double(b.x) * Double(b.x) + Double(b.y) * Double(b.y)
            let weighted = (x == 0 ? 1.0 : 2.0) * power
            energy += weighted; wiwi += weighted * Double(x * x + y * y)
            w[i] = SIMD2(-Float(y) * a.y + Float(x) * b.y, Float(y) * a.x - Float(x) * b.x)
        } }
        return (energy, wiwi, state)
    }

    private static func reshapeKM3(n: Int, k0: Double, seed: Int, velocity u: UnsafeMutablePointer<SIMD2<Float>>) {
        let half = n / 2, count = n * half
        func hash(_ x: Int, _ z: Int, _ salt: Int) -> Double {
            let a = (Double(x) + 1) * 12.9898 + (Double(z) + 1) * 78.233 + (Double(seed) + 1) * 0.010001 + (Double(salt) + 1) * 37.719
            let s = sin(a) * 43758.5453123
            return s - floor(s)
        }
        var shells = [Double](repeating: 0, count: half + 1), weights = shells, target = shells
        for x in 0..<half { for z in 0..<n {
            let y = Double(z < half ? z : z - n), k = sqrt(Double(x * x) + y * y), shell = Int(k + 0.5)
            if k > 0 && k <= Double(half) && shell <= half {
                let i = z * half + x, a = u[i], b = u[count + i], multiplicity = x == 0 ? 1.0 : 2.0
                let power = Double(a.x) * Double(a.x) + Double(a.y) * Double(a.y) + Double(b.x) * Double(b.x) + Double(b.y) * Double(b.y)
                shells[shell] += multiplicity * power
                weights[shell] += multiplicity * (0.75 + 0.50 * hash(x, x == 0 && z > half ? n - z : z, 17))
            }
        } }
        var original = 0.0, total = 0.0
        let k0 = max(1, k0)
        for shell in 1...half {
            let k = Double(shell), low = k / k0
            target[shell] = k < k0 ? (low * low * low * low) / (k0 * k0 * k0) : 1 / (k * k * k)
            original += shells[shell]; total += target[shell]
        }
        guard original > 0 && total > 0 else { return }
        let norm = original / total
        for x in 0..<half { for z in 0..<n {
            let y = Double(z < half ? z : z - n), k = sqrt(Double(x * x) + y * y), shell = Int(k + 0.5)
            let zh = x == 0 && z > half ? n - z : z
            var a = SIMD2<Float>.zero, b = SIMD2<Float>.zero
            if k > 0 && k <= Double(half) && shell <= half && weights[shell] > 0 {
                let power = norm * target[shell] * (0.75 + 0.50 * hash(x, zh, 17)) / weights[shell]
                let phase = (x == 0 && z > half ? -1.0 : 1.0) * 2 * Double.pi * hash(x, zh, 53)
                let direction = SIMD2(Float(cos(phase)), Float(sin(phase)))
                if x == 0 { a = Float(sqrt(power)) * direction }
                else { b = Float(sqrt(power / (1 + y * y / Double(x * x)))) * direction; a = Float(-y / Double(x)) * b }
            }
            u[z * half + x] = a; u[count + z * half + x] = b
        } }
    }
}
