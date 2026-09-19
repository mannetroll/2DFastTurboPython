import Foundation
import Metal
import simd

/// A GPU-resident decaying, dealiased vorticity solver. All mutation and submission
/// belong to executionQueue. Public asynchronous completions run on that queue.
public final class FastTurboCore: @unchecked Sendable {
    public let device: MTLDevice
    public let configuration: FastTurboConfiguration
    private let executionQueue = DispatchQueue(label: "2DFastTurbo.solver", qos: .userInteractive)
    private let executionQueueKey = DispatchSpecificKey<UInt8>()
    private let commandQueue: MTLCommandQueue
    private let pipelines: [String: MTLComputePipelineState]
    private let paddedFFT: MTTVkFFTBackend
    private let snapshotFFT: MTTVkFFTBackend
    private var inputFFT: MTTVkFFTBackend?
    private let omega: MTLBuffer
    private let correction: MTLBuffer
    private let padded: MTLBuffer
    private let base: MTLBuffer
    private let first: MTLBuffer
    private let second: MTLBuffer
    private let auxiliary: MTLBuffer
    private let coefficients: MTLBuffer
    private let clock: MTLBuffer
    private let cflPartials: MTLBuffer
    private let statisticPartials: MTLBuffer
    private let snapshotSpectra: MTLBuffer
    private let snapshotPartials: MTLBuffer
    private let snapshotBuffers: [MTLBuffer]
    private var freeSlots = [0, 1, 2]
    private let allocatedBytes: Int
    private var cachedEnergy = 0.0
    private var cachedEnstrophy = 0.0
    private var cachedPalinstrophy = 0.0
    private var cachedFinite = true
    private var activeReynolds: Double
    private var activeCFL: Float
    private var initialTurnoverTime = 0.0
    private var lastGPUMilliseconds = 0.0
    private var terminalFailure: Error?
    private static let groups = 64

    public init(configuration: FastTurboConfiguration, device requestedDevice: MTLDevice? = nil,
                vorticity: [Float]? = nil) throws {
        let n = configuration.resolution
        guard n >= 16, n <= 16384, n % 2 == 0 else {
            throw FastTurboError.invalidConfiguration("Resolution must be even, between 16 and 16384.")
        }
        guard configuration.reynolds > 0, !configuration.reynolds.isNaN,
              configuration.viscosity.isFinite, configuration.cfl > 0, configuration.cfl.isFinite,
              configuration.initialWaveNumber > 0,
              configuration.fixedTimeStep.map({ $0 > 0 && $0.isFinite }) ?? true else {
            throw FastTurboError.invalidConfiguration("Reynolds number, wave number, CFL and optional fixed timestep must be positive.")
        }
        guard let device = requestedDevice ?? MTLCreateSystemDefaultDevice() else { throw FastTurboError.noMetalDevice }
        guard let commandQueue = device.makeCommandQueue() else { throw FastTurboError.metal("Cannot create a Metal command queue.") }
        self.device = device
        self.configuration = configuration
        self.activeReynolds = configuration.reynolds
        self.activeCFL = configuration.cfl
        self.commandQueue = commandQueue
        commandQueue.label = "2DFastTurbo simulation and snapshots"

        let options = MTLCompileOptions()
        // Safe scalar arithmetic preserves compensated time and small-z ETD coefficients.
        // VkFFT independently compiles its optimized transform kernels.
        options.mathMode = .safe
        let library = try device.makeLibrary(source: FastTurboShaders.source, options: options)
        // These values are immutable for this core; keep timestep and stage data dynamic.
        let functionConstants = MTLFunctionConstantValues()
        var resolutionConstant = UInt32(configuration.resolution)
        var paddedResolutionConstant = UInt32(configuration.paddedResolution)
        var integratorConstant = configuration.integrator.shaderIndex
        var formulationConstant = configuration.nonlinearFormulation.shaderIndex
        functionConstants.setConstantValue(&resolutionConstant, type: .uint, index: 0)
        functionConstants.setConstantValue(&paddedResolutionConstant, type: .uint, index: 1)
        functionConstants.setConstantValue(&integratorConstant, type: .uint, index: 2)
        functionConstants.setConstantValue(&formulationConstant, type: .uint, index: 3)
        var pipelines: [String: MTLComputePipelineState] = [:]
        for name in ["projectState", "velocityToPadded", "reduceCFLAndProducts", "selectTimeStep", "nonlinearProducts",
                     "advanceStage", "commitStep", "spectralStatistics", "prepareSnapshot", "extractSnapshot"] {
            let function = try library.makeFunction(name: name, constantValues: functionConstants)
            pipelines[name] = try device.makeComputePipelineState(function: function)
        }
        self.pipelines = pipelines
        let m = configuration.paddedResolution
        let paddedComplexPitch = ((m / 2 + 1 + 7) / 8) * 8
        // Padded transforms only consume/retain |kx|, |ky| < N/2. Snapshot and input
        // transforms keep the unrestricted initializer and their full output.
        paddedFFT = MTTVkFFTBackend(device: device, commandQueue: commandQueue, resolution: UInt(m),
                                   realToComplex: true, retainedXCutoff: UInt(n / 2), retainedYCutoff: UInt(n / 2),
                                   complexRowStride: UInt(paddedComplexPitch))
        snapshotFFT = MTTVkFFTBackend(device: device, commandQueue: commandQueue, resolution: UInt(n), realToComplex: true)
        guard paddedFFT.isAvailable, snapshotFFT.isAvailable else {
            throw FastTurboError.metal(paddedFFT.lastErrorMessage ?? snapshotFFT.lastErrorMessage ?? "VkFFT unavailable.")
        }
        var bytes = 0
        func allocate(_ length: Int, _ label: String) throws -> MTLBuffer {
            guard let buffer = device.makeBuffer(length: length, options: .storageModeShared) else {
                throw FastTurboError.metal("Cannot allocate \(label) (\(length) bytes).")
            }
            buffer.label = label
            bytes += length
            return buffer
        }
        // Real fields need only kx=0...N/2, with every ky row. The missing
        // negative-kx modes are their conjugates at the opposite ky.
        let stateCount = n * (n / 2 + 1)
        let stateBytes = stateCount * MemoryLayout<SIMD2<Float>>.stride
        omega = try allocate(stateBytes, "Canonical Hermitian vorticity spectrum")
        correction = try allocate(stateBytes, "Compensated vorticity increment remainder")
        let paddedFields = configuration.nonlinearFormulation == .stress ? 2 : 4
        padded = try allocate(paddedFields * m * paddedComplexPitch * 8, "Padded nonlinear R2C fields")
        first = try allocate(stateBytes, "First / previous nonlinear term")
        base = try allocate(configuration.integrator == .imexRK3 ? 8 : stateBytes, "Timestep initial spectrum")
        second = try allocate(configuration.integrator == .etdrk4 ? stateBytes : 8, "ETD middle nonlinear sum")
        auxiliary = try allocate(configuration.integrator == .etdrk4 ? stateBytes : 8, "ETD first stage")
        let coefficientCount = configuration.integrator == .etdrk4 ? 6 : (configuration.integrator == .ifHeunRK3 ? 3 : 0)
        coefficients = try allocate(max(4, coefficientCount * stateCount * 4), "Exponential coefficients")
        clock = try allocate(MemoryLayout<GPUClock>.stride, "GPU timestep and compensated clock")
        cflPartials = try allocate(Self.groups * MemoryLayout<SIMD2<Float>>.stride, "CFL reduction partials")
        statisticPartials = try allocate(Self.groups * MemoryLayout<SIMD4<Float>>.stride, "Spectral diagnostics")
        snapshotSpectra = try allocate(3 * n * (n / 2 + 1) * 8, "Snapshot R2C spectra")
        snapshotPartials = try allocate(2 * Self.groups * MemoryLayout<SIMD4<Float>>.stride, "Snapshot extrema and moments")
        snapshotBuffers = try (0..<3).map { try allocate(3 * n * n * 4, "Immutable snapshot slot \($0)") }
        allocatedBytes = bytes
        precondition(MemoryLayout<GPUClock>.stride == 40 && MemoryLayout<GPUParams>.stride == 52)
        executionQueue.setSpecific(key: executionQueueKey, value: 1)
        try resetOwned(vorticity: vorticity)
    }

    public func advance(steps: Int, until: Double? = nil,
                        completion: @escaping @Sendable (Result<FastTurboStatistics, Error>) -> Void) {
        executionQueue.async { [self] in
            completion(autoreleasepool { Result { try advanceOwned(steps: steps, until: until) } })
        }
    }

    public func inspect(completion: @escaping @Sendable (Result<FastTurboStatistics, Error>) -> Void) {
        executionQueue.async { [self] in completion(autoreleasepool { Result { try inspectOwned() } }) }
    }

    public func snapshot(includeVelocity: Bool,
                         completion: @escaping @Sendable (Result<FastTurboFrame?, Error>) -> Void) {
        executionQueue.async { [self] in
            completion(autoreleasepool { Result { try snapshotOwned(includeVelocity: includeVelocity) } })
        }
    }

    public func advanceSync(steps: Int, until: Double? = nil) throws -> FastTurboStatistics {
        try withExecutionQueue { try autoreleasepool { try advanceOwned(steps: steps, until: until) } }
    }

    public func inspectSync() throws -> FastTurboStatistics {
        try withExecutionQueue { try autoreleasepool { try inspectOwned() } }
    }

    public func snapshotSync(includeVelocity: Bool = true) throws -> FastTurboFrame? {
        try withExecutionQueue { try autoreleasepool { try snapshotOwned(includeVelocity: includeVelocity) } }
    }

    /// Changes viscosity between completed batches without rebuilding the solver.
    /// The configuration remains the run's initial configuration; statistics report
    /// this effective value so a UI controller can display its live state.
    public func setReynoldsSync(_ reynolds: Double) throws {
        try withExecutionQueue {
            guard reynolds.isFinite, reynolds > 0, Float(1 / reynolds).isFinite else {
                throw FastTurboError.invalidConfiguration("Reynolds number must be finite and positive.")
            }
            activeReynolds = reynolds
        }
    }

    /// Queues a valid manual Reynolds update behind any in-flight GPU batch.
    /// This avoids rebuilding FFT plans while a slider is being dragged.
    public func setReynolds(_ reynolds: Double) {
        executionQueue.async { [self] in
            guard reynolds.isFinite, reynolds > 0, Float(1 / reynolds).isFinite else { return }
            activeReynolds = reynolds
        }
    }

    /// Updates the CFL factor at a completed batch boundary. The next step uses
    /// it in the existing adaptive timestep selector; field and clock are kept.
    public func setCFLSync(_ cfl: Float) throws {
        try withExecutionQueue {
            guard cfl.isFinite, cfl > 0 else {
                throw FastTurboError.invalidConfiguration("CFL must be finite and positive.")
            }
            activeCFL = cfl
        }
    }

    /// Restores the same configuration without rebuilding plans or pipelines. Existing
    /// frame leases remain immutable. A physical input is projected onto the retained band.
    public func resetSync(vorticity: [Float]? = nil) throws {
        try withExecutionQueue { try autoreleasepool { try resetOwned(vorticity: vorticity) } }
    }

    /// Full row-major [ky,kx] Fourier-series coefficients: omega(x,y) = sum_k
    /// spectrum[k] exp(i k.x). Unlike the internal FFT buffer, this divides by N².
    public func spectrumSync() throws -> [SIMD2<Float>] {
        try withExecutionQueue {
            try requireHealthyState()
            let n = configuration.resolution, width = n / 2 + 1
            let count = n * n
            let pointer = omega.contents().bindMemory(to: SIMD2<Float>.self, capacity: n * width)
            let scale = 1 / Float(count)
            return (0..<count).map { index in
                let x = index % n, y = index / n
                if x < width { return pointer[y * width + x] * scale }
                let value = pointer[((n - y) % n) * width + (n - x)]
                return SIMD2(value.x, -value.y) * scale
            }
        }
    }

    /// Completion handlers already run on executionQueue. Synchronous inspection
    /// from those handlers must not dispatch synchronously back onto the same queue.
    private func withExecutionQueue<T>(_ body: () throws -> T) rethrows -> T {
        if DispatchQueue.getSpecific(key: executionQueueKey) != nil { return try body() }
        return try executionQueue.sync(execute: body)
    }

    private func requireHealthyState() throws {
        if let terminalFailure { throw terminalFailure }
    }

    private func invalidateState(_ error: Error) -> Error {
        terminalFailure = error
        cachedFinite = false
        return error
    }

    private func params(stage: Int = 0, until: Double? = nil) -> GPUParams {
        let high = Float(until ?? 0)
        return GPUParams(n: UInt32(configuration.resolution), m: UInt32(configuration.paddedResolution),
                         integrator: configuration.integrator.shaderIndex, stage: UInt32(stage),
                         viscosity: Float(1 / activeReynolds), cfl: activeCFL,
                         fixedDT: configuration.fixedTimeStep ?? 0,
                         inverseN2: 1 / Float(configuration.resolution * configuration.resolution),
                         endHigh: high, endLow: Float((until ?? 0) - Double(high)),
                         hasEnd: until == nil ? 0 : 1, groups: UInt32(Self.groups),
                         nonlinearFormulation: configuration.nonlinearFormulation.shaderIndex)
    }

    private func command(_ label: String) throws -> MTLCommandBuffer {
        guard let command = commandQueue.makeCommandBuffer() else { throw FastTurboError.metal("Cannot create a command buffer.") }
        command.label = label
        return command
    }

    private func encoder(_ name: String, _ command: MTLCommandBuffer) throws -> MTLComputeCommandEncoder {
        guard let encoder = command.makeComputeCommandEncoder(), let pipeline = pipelines[name] else {
            throw FastTurboError.metal("Cannot encode \(name).")
        }
        encoder.label = name
        encoder.setComputePipelineState(pipeline)
        return encoder
    }

    private func setParams(_ params: GPUParams, at index: Int, encoder: MTLComputeCommandEncoder) {
        var value = params
        encoder.setBytes(&value, length: MemoryLayout<GPUParams>.stride, index: index)
    }

    private func dispatch(_ encoder: MTLComputeCommandEncoder, width: Int, height: Int) {
        encoder.dispatchThreads(MTLSize(width: width, height: height, depth: 1),
                                threadsPerThreadgroup: MTLSize(width: 16, height: 16, depth: 1))
        encoder.endEncoding()
    }

    private func dispatchReduction(_ encoder: MTLComputeCommandEncoder) {
        encoder.dispatchThreadgroups(MTLSize(width: Self.groups, height: 1, depth: 1),
                                     threadsPerThreadgroup: MTLSize(width: 256, height: 1, depth: 1))
        encoder.endEncoding()
    }

    private func dispatchScalar(_ encoder: MTLComputeCommandEncoder) {
        encoder.dispatchThreads(MTLSize(width: 1, height: 1, depth: 1),
                                threadsPerThreadgroup: MTLSize(width: 1, height: 1, depth: 1))
        encoder.endEncoding()
    }

    @discardableResult private func execute(_ command: MTLCommandBuffer) throws -> Double {
        command.commit()
        command.waitUntilCompleted()
        if let error = command.error { throw invalidateState(error) }
        guard command.status == .completed else {
            throw invalidateState(FastTurboError.metal("Metal command did not complete."))
        }
        return max(0, command.gpuEndTime - command.gpuStartTime) * 1000
    }

    private func transform(_ backend: MTTVkFFTBackend, buffer: MTLBuffer, batches: Int,
                           inverse: Bool, command: MTLCommandBuffer) throws {
        guard backend.encode(buffer: buffer, byteOffset: 0, batchCount: UInt(batches),
                             inverse: inverse, commandBuffer: command) else {
            throw FastTurboError.metal(backend.lastErrorMessage ?? "VkFFT encoding failed.")
        }
    }

    private func encodeVelocity(_ command: MTLCommandBuffer, params: GPUParams) throws {
        let scatter = try encoder("velocityToPadded", command)
        scatter.setBuffer(omega, offset: 0, index: 0)
        scatter.setBuffer(padded, offset: 0, index: 1)
        setParams(params, at: 2, encoder: scatter)
        dispatch(scatter, width: configuration.paddedResolution / 2 + 1, height: configuration.paddedResolution)
        let fields = configuration.nonlinearFormulation == .stress ? 2 : 4
        try transform(paddedFFT, buffer: padded, batches: fields, inverse: true, command: command)
    }

    private func encodeCFL(_ command: MTLCommandBuffer, params: GPUParams) throws {
        let reduce = try encoder("reduceCFLAndProducts", command)
        reduce.setBuffer(padded, offset: 0, index: 0)
        reduce.setBuffer(cflPartials, offset: 0, index: 1)
        setParams(params, at: 2, encoder: reduce)
        dispatchReduction(reduce)
        let select = try encoder("selectTimeStep", command)
        select.setBuffer(cflPartials, offset: 0, index: 0)
        select.setBuffer(clock, offset: 0, index: 1)
        setParams(params, at: 2, encoder: select)
        dispatchScalar(select)
    }

    private func encodeStep(_ command: MTLCommandBuffer, until: Double?) throws {
        let stages = configuration.integrator == .etdrk4 ? 4 : 3
        for stage in 0..<stages {
            let p = params(stage: stage, until: until)
            try encodeVelocity(command, params: p)
            if stage == 0 { try encodeCFL(command, params: p) }
            if stage > 0 {
                let products = try encoder("nonlinearProducts", command)
                products.setBuffer(padded, offset: 0, index: 0)
                setParams(p, at: 1, encoder: products)
                dispatch(products, width: configuration.paddedResolution, height: configuration.paddedResolution)
            }
            let products = configuration.nonlinearFormulation == .stress ? 2 : 1
            try transform(paddedFFT, buffer: padded, batches: products, inverse: false, command: command)
            let update = try encoder("advanceStage", command)
            for (index, buffer) in [omega, padded, base, first, second, auxiliary, coefficients, clock, correction].enumerated() {
                update.setBuffer(buffer, offset: 0, index: index)
            }
            setParams(p, at: 9, encoder: update)
            dispatch(update, width: configuration.resolution / 2 + 1, height: configuration.resolution)
        }
        let commit = try encoder("commitStep", command)
        commit.setBuffer(clock, offset: 0, index: 0)
        setParams(params(until: until), at: 1, encoder: commit)
        dispatchScalar(commit)
    }

    private func advanceOwned(steps: Int, until: Double?) throws -> FastTurboStatistics {
        guard steps >= 0 else { throw FastTurboError.invalidConfiguration("Step count cannot be negative.") }
        if let until, (!until.isFinite || until < 0 || !Float(until).isFinite) {
            throw FastTurboError.invalidConfiguration("Target time must be finite and nonnegative.")
        }
        try requireHealthyState()
        if steps == 0 || (until.map { $0 <= readClockTime() } ?? false) { return statisticsOwned() }
        var pending = steps
        var milliseconds = 0.0
        // Bound host command-buffer storage even for a large headless request. This
        // has no effect on timestep selection or numerical evolution.
        while pending > 0 {
            let count = min(pending, 256)
            let command = try command("\(configuration.integrator.title): \(count) steps")
            for _ in 0..<count { try encodeStep(command, until: until) }
            milliseconds += try execute(command)
            pending -= count
            if clock.contents().load(as: GPUClock.self).valid == 0 {
                throw invalidateState(FastTurboError.nonFiniteState)
            }
            if let until, readClockTime() >= until - max(1, until) * 1e-14 { break }
        }
        lastGPUMilliseconds = milliseconds
        let statistics = statisticsOwned()
        guard statistics.isFinite else { throw invalidateState(FastTurboError.nonFiniteState) }
        return statistics
    }

    private func inspectOwned() throws -> FastTurboStatistics {
        try requireHealthyState()
        let command = try command("Spectral energy and enstrophy")
        let reduce = try encoder("spectralStatistics", command)
        reduce.setBuffer(omega, offset: 0, index: 0)
        reduce.setBuffer(statisticPartials, offset: 0, index: 1)
        setParams(params(), at: 2, encoder: reduce)
        dispatchReduction(reduce)
        try execute(command)
        let pointer = statisticPartials.contents().bindMemory(to: SIMD4<Float>.self, capacity: Self.groups)
        cachedEnergy = 0; cachedEnstrophy = 0; cachedPalinstrophy = 0; cachedFinite = true
        for index in 0..<Self.groups {
            cachedEnergy += Double(pointer[index].x)
            cachedEnstrophy += Double(pointer[index].y)
            cachedPalinstrophy += Double(pointer[index].w)
            cachedFinite = cachedFinite && pointer[index].z == 0
        }
        cachedFinite = cachedFinite && cachedEnergy.isFinite && cachedEnstrophy.isFinite && cachedPalinstrophy.isFinite
        let statistics = statisticsOwned()
        guard statistics.isFinite else { throw invalidateState(FastTurboError.nonFiniteState) }
        return statistics
    }

    private func statisticsOwned() -> FastTurboStatistics {
        let state = clock.contents().load(as: GPUClock.self)
        var stats = FastTurboStatistics()
        stats.step = state.steps
        stats.time = Double(state.timeHigh) + Double(state.timeLow)
        stats.timeStep = state.dt
        stats.energy = cachedEnergy
        stats.enstrophy = cachedEnstrophy
        stats.palinstrophy = cachedPalinstrophy
        let maximumWaveNumber = Double(configuration.resolution / 2 - 1)
        let kmaxSquared = 2 * maximumWaveNumber * maximumWaveNumber
        stats.palinstrophyOverEnstrophyKmaxSquared = cachedEnstrophy > 0 && kmaxSquared > 0
            ? cachedPalinstrophy / (cachedEnstrophy * kmaxSquared) : 0
        stats.reynolds = activeReynolds
        stats.initialTurnoverTime = initialTurnoverTime
        stats.gpuMilliseconds = lastGPUMilliseconds
        stats.allocatedBytes = allocatedBytes
        stats.isFinite = cachedFinite && state.valid != 0 && stats.time.isFinite && stats.timeStep.isFinite
        return stats
    }

    private func readClockTime() -> Double {
        let state = clock.contents().load(as: GPUClock.self)
        return Double(state.timeHigh) + Double(state.timeLow)
    }

    private func snapshotOwned(includeVelocity: Bool) throws -> FastTurboFrame? {
        try requireHealthyState()
        guard let slot = freeSlots.popLast() else { return nil }
        do {
            let fields = includeVelocity ? 3 : 1
            let p = params(stage: fields)
            let command = try command("Committed-state snapshot")
            let prepare = try encoder("prepareSnapshot", command)
            prepare.setBuffer(omega, offset: 0, index: 0)
            prepare.setBuffer(snapshotSpectra, offset: 0, index: 1)
            setParams(p, at: 2, encoder: prepare)
            dispatch(prepare, width: configuration.resolution / 2 + 1, height: configuration.resolution)
            try transform(snapshotFFT, buffer: snapshotSpectra, batches: fields, inverse: true, command: command)
            let extract = try encoder("extractSnapshot", command)
            extract.setBuffer(snapshotSpectra, offset: 0, index: 0)
            extract.setBuffer(snapshotBuffers[slot], offset: 0, index: 1)
            extract.setBuffer(snapshotPartials, offset: 0, index: 2)
            setParams(p, at: 3, encoder: extract)
            dispatchReduction(extract)
            try execute(command)
            let pointer = snapshotPartials.contents().bindMemory(to: SIMD4<Float>.self, capacity: 2 * Self.groups)
            var maxOmega: Float = 0, maxSpeed: Float = 0
            var sumOmega = 0.0, sumOmegaSquared = 0.0
            for i in 0..<Self.groups {
                guard pointer[i].z == 0 else { throw invalidateState(FastTurboError.nonFiniteState) }
                maxOmega = max(maxOmega, pointer[i].x); maxSpeed = max(maxSpeed, pointer[i].y)
                let moments = pointer[Self.groups + i]
                sumOmega += Double(moments.x); sumOmegaSquared += Double(moments.y)
            }
            let count = Double(configuration.resolution * configuration.resolution)
            let meanOmega = sumOmega / count
            let standardDeviationOmega = sqrt(max(sumOmegaSquared / count - meanOmega * meanOmega, 0))
            let stats = statisticsOwned()
            guard stats.isFinite, maxOmega.isFinite, maxSpeed.isFinite,
                  meanOmega.isFinite, standardDeviationOmega.isFinite else {
                throw invalidateState(FastTurboError.nonFiniteState)
            }
            return FastTurboFrame(buffer: snapshotBuffers[slot], gridSize: configuration.resolution,
                                  sequence: stats.step, time: stats.time, maxVorticity: maxOmega,
                                  meanVorticity: Float(meanOmega), standardDeviationVorticity: Float(standardDeviationOmega),
                                  maxSpeed: maxSpeed) { [weak self] in
                self?.releaseSlot(slot)
            }
        } catch {
            freeSlots.append(slot)
            throw error
        }
    }

    private func releaseSlot(_ slot: Int) {
        executionQueue.async { [self] in freeSlots.append(slot) }
    }

    private func resetOwned(vorticity: [Float]?) throws {
        let n = configuration.resolution
        if let vorticity, vorticity.count != n * n || !vorticity.allSatisfy(\.isFinite) {
            throw FastTurboError.invalidConfiguration("Initial vorticity must contain N² finite physical samples.")
        }
        // Reset may recover a terminal failure. Only this serial operation can
        // see the temporarily cleared latch; a failed reset invalidates it again.
        terminalFailure = nil
        do {
            try restoreStateOwned(vorticity: vorticity)
        } catch {
            throw invalidateState(error)
        }
    }

    private func restoreStateOwned(vorticity: [Float]?) throws {
        let n = configuration.resolution
        let pointer = omega.contents().bindMemory(to: SIMD2<Float>.self, capacity: n * (n / 2 + 1))
        if let vorticity {
            // Retain the existing C2C import transform and its normalization.
            // This full-size staging buffer lives only until import completes.
            let inputBytes = n * n * MemoryLayout<SIMD2<Float>>.stride
            guard let input = device.makeBuffer(length: inputBytes, options: .storageModeShared) else {
                throw FastTurboError.metal("Cannot allocate the initial-field FFT staging buffer.")
            }
            input.label = "Temporary full-spectrum initial-field import"
            let samples = input.contents().bindMemory(to: SIMD2<Float>.self, capacity: n * n)
            for i in 0..<(n * n) { samples[i] = SIMD2(vorticity[i], 0) }
            if inputFFT == nil {
                inputFFT = MTTVkFFTBackend(device: device, commandQueue: commandQueue, resolution: UInt(n), realToComplex: false)
            }
            guard let inputFFT, inputFFT.isAvailable else { throw FastTurboError.metal("Cannot create the initial-field FFT.") }
            let command = try command("Import canonical physical vorticity")
            try transform(inputFFT, buffer: input, batches: 1, inverse: false, command: command)
            let project = try encoder("projectState", command)
            project.setBuffer(input, offset: 0, index: 0)
            project.setBuffer(omega, offset: 0, index: 1)
            setParams(params(), at: 2, encoder: project)
            dispatch(project, width: n / 2 + 1, height: n)
            try execute(command)
        } else {
            writePao(to: pointer)
            if configuration.startSpectrum == .km3 { reshapeKM3(pointer) }
        }
        clock.contents().storeBytes(of: GPUClock(), as: GPUClock.self)
        memset(correction.contents(), 0, correction.length)
        memset(first.contents(), 0, first.length)
        lastGPUMilliseconds = 0
        initialTurnoverTime = 0
        let command = try command("Initial GPU CFL")
        try encodeVelocity(command, params: params())
        try encodeCFL(command, params: params())
        try execute(command)
        let statistics = try inspectOwned()
        guard statistics.isFinite else { throw FastTurboError.nonFiniteState }
        if statistics.energy > 0, statistics.enstrophy > 0 {
            let length = 2 * Double.pi * sqrt(statistics.energy / statistics.enstrophy)
            initialTurnoverTime = length / sqrt(2 * statistics.energy)
        }
    }

    // Same shell redistribution as the CUDA initializer in km3.hpp. The
    // existing unnormalized positive-curl spectrum supplies the PAO energy.
    private func reshapeKM3(_ pointer: UnsafeMutablePointer<SIMD2<Float>>) {
        let n = configuration.resolution, half = n / 2, width = n / 2 + 1
        let scale = Float(n * n), k0 = max(1, Double(configuration.initialWaveNumber))
        func hash(_ x: Int, _ z: Int, _ salt: Int) -> Double {
            let a = (Double(x) + 1) * 12.9898 + (Double(z) + 1) * 78.233
                + (Double(configuration.seed) + 1) * 0.010001 + (Double(salt) + 1) * 37.719
            let s = sin(a) * 43758.5453123
            return s - floor(s)
        }
        var shells = [Double](repeating: 0, count: half + 1)
        var weights = shells, target = shells
        for x in 0..<half {
            for z in 0..<n {
                let y = Double(z < half ? z : z - n), k2 = Double(x * x) + y * y
                let k = sqrt(k2), shell = Int(k + 0.5), zh = x == 0 && z > half ? n - z : z
                if k > 0 && k <= Double(half) && shell <= half {
                    let w = pointer[z * width + x], multiplicity = x == 0 ? 1.0 : 2.0
                    let power = Double(w.x) * Double(w.x) + Double(w.y) * Double(w.y)
                    shells[shell] += multiplicity * power / (Double(scale) * Double(scale) * k2)
                    weights[shell] += multiplicity * (0.75 + 0.50 * hash(x, zh, 17))
                }
            }
        }
        var original = 0.0, total = 0.0
        for shell in 1...half {
            let k = Double(shell), low = k / k0
            target[shell] = k < k0 ? (low * low * low * low) / (k0 * k0 * k0) : 1 / (k * k * k)
            original += shells[shell]
            total += target[shell]
        }
        guard original > 0 && total > 0 else { return }
        let norm = original / total
        for x in 0..<half {
            for z in 0..<n {
                let y = Double(z < half ? z : z - n), k = sqrt(Double(x * x) + y * y)
                let shell = Int(k + 0.5), zh = x == 0 && z > half ? n - z : z
                var u = SIMD2<Float>.zero, v = SIMD2<Float>.zero
                if k > 0 && k <= Double(half) && shell <= half && weights[shell] > 0 {
                    let power = norm * target[shell] * (0.75 + 0.50 * hash(x, zh, 17)) / weights[shell]
                    let sign = x == 0 && z > half ? -1.0 : 1.0
                    let phase = sign * 2 * Double.pi * hash(x, zh, 53)
                    let direction = SIMD2<Float>(Float(cos(phase)), Float(sin(phase)))
                    if x == 0 { u = Float(sqrt(power)) * direction }
                    else {
                        v = Float(sqrt(power / (1 + y * y / Double(x * x)))) * direction
                        u = Float(-y / Double(x)) * v
                    }
                }
                let curl = v * Float(x) - u * Float(y)
                pointer[z * width + x] = z == half ? .zero : SIMD2(-curl.y, curl.x) * scale
            }
        }
    }

    private func writePao(to pointer: UnsafeMutablePointer<SIMD2<Float>>) {
        let n = configuration.resolution, half = n / 2
        let width = half + 1
        memset(pointer, 0, n * width * MemoryLayout<SIMD2<Float>>.stride)
        var generator = SplitMix64(seed: configuration.seed)
        let k0 = Float(configuration.initialWaveNumber)
        let normalization = Float.pi * k0 * k0, scale = Float(n * n)
        for iy in 0..<n {
            let ky = iy <= half ? iy : iy - n
            if abs(ky) >= half { continue }
            for kx in 0..<half {
                if kx == 0 && ky <= 0 { continue }
                let k2 = Float(kx * kx + ky * ky)
                let amplitude = exp(-k2 / (k0 * k0)) / normalization
                let phase = 2 * Float.pi * generator.nextFloat()
                let direction = SIMD2<Float>(cos(phase), sin(phase))
                let u: SIMD2<Float>, v: SIMD2<Float>
                if kx == 0 { u = direction * sqrt(amplitude); v = .zero }
                else {
                    let ratio = Float(ky) / Float(kx)
                    v = direction * sqrt(amplitude / (1 + ratio * ratio))
                    u = v * -ratio
                }
                // Preserve the legacy realization's physical velocity while flipping
                // its vorticity convention to omega = dx(v) - dy(u).
                let curl = v * Float(kx) - u * Float(ky)
                let w = SIMD2<Float>(-curl.y, curl.x) * scale
                let sy = ky >= 0 ? ky : ky + n
                pointer[sy * width + kx] = w
                // Only the kx=0 conjugate also belongs to the stored half.
                if kx == 0 { pointer[((n - sy) % n) * width] = SIMD2(w.x, -w.y) }
            }
        }
    }
}

private struct GPUClock {
    var timeHigh: Float = 0
    var timeLow: Float = 0
    var dt: Float = 0
    var controllerDT: Float = 0
    var steps: UInt64 = 0
    var maxSpeed: Float = 0
    var valid: UInt32 = 1
    var active: UInt32 = 0
    var padding: UInt32 = 0
}

private struct GPUParams {
    var n, m, integrator, stage: UInt32
    var viscosity, cfl, fixedDT, inverseN2: Float
    var endHigh, endLow: Float
    var hasEnd, groups: UInt32
    var nonlinearFormulation: UInt32
}

private struct SplitMix64 {
    var state: UInt64
    init(seed: UInt64) { state = seed &+ 0x9e3779b97f4a7c15 }
    mutating func nextFloat() -> Float {
        state &+= 0x9e3779b97f4a7c15
        var value = state
        value = (value ^ (value >> 30)) &* 0xbf58476d1ce4e5b9
        value = (value ^ (value >> 27)) &* 0x94d049bb133111eb
        value ^= value >> 31
        return Float(value >> 40) / Float(1 << 24)
    }
}
