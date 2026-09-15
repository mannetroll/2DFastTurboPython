import AppKit
import Combine
import CryptoKit
import Foundation
import Metal
import UniformTypeIdentifiers

/// The viscosity regulator used by the palinstrophy simulator. It intentionally
/// operates on diagnostic samples, rather than adding a readback to every step.
private struct PalinstrophyReynoldsController {
    static let target = 20.0
    static let minimumReynolds = 1_000.0
    static let maximumReynolds = 1_000_000.0
    private let proportionalGain = 0.25
    private let integralGain = 0.0
    private let derivativeGain = 25.0
    private var previousError = 0.0
    private var integral = 0.0
    private var firstFilter = 0.0
    private var filtered = 0.0

    mutating func reset() {
        previousError = 0
        integral = 0
        firstFilter = 0
        filtered = 0
    }

    mutating func nextReynolds(current: Double, metric: Double, resolution: Int,
                               initialWaveNumber: Int) -> Double? {
        let raw = 10_000 * metric
        guard raw.isFinite, raw > 0, current.isFinite, current > 0 else { return nil }

        // Two cascaded first-order EMAs, tau = five diagnostic calls.
        let alpha = 1.0 / 6.0
        if filtered == 0 {
            firstFilter = raw
            filtered = raw
        } else {
            firstFilter += alpha * (raw - firstFilter)
            filtered += alpha * (firstFilter - filtered)
        }

        let error = (filtered - Self.target) / Self.target
        guard abs(error) >= 0.001 else { return nil }
        if integralGain != 0 { integral += error }
        let derivative = error - previousError
        previousError = error
        let output = max(-0.01, min(0.01,
                                    proportionalGain * error + integralGain * integral + derivativeGain * derivative))
        let reference = FastTurboConfiguration.reynoldsFromNAndK0(resolution, initialWaveNumber)
        let allowedMinimum = max(Self.minimumReynolds, reference / 1_000)
        let allowedMaximum = min(Self.maximumReynolds, reference * 1_000)
        return min(allowedMaximum, max(allowedMinimum, current * exp(output)))
    }
}

/// All mutable scheduling state lives on this queue. GPU completion schedules
/// the next batch directly; the main run loop is only a consumer of results.
private final class SimulationPump: @unchecked Sendable {
    struct Run {
        var configuration: FastTurboConfiguration
        var palinstrophyRegulated: Bool
        let generation: UInt64
        let until: Double?
        let targetWallSeconds: Double?
        let initialField: URL?
        let spectrumOutput: URL?
        let fixedBatch: Int?
    }
    struct Delivery {
        let statistics: FastTurboStatistics
        let performance: TurboPerformance
        let elapsed: Double
        let generation: UInt64
        let finished: Bool
    }
    private let queue = DispatchQueue(label: "com.mannetroll.FastTurbo.pump", qos: .userInitiated)
    private var core: FastTurboCore?
    private var replacement: Run?
    private var current: Run?
    private var visible = true
    private var running = true
    private var busy = false
    private var pendingSteps = 0
    private var includeVelocity = false
    private var needsFrame = false
    private var needsInspection = false
    private var initialized = false
    private var finishedExported = false
    private var finalFrameReady = false
    private var finished = false
    private var statistics: FastTurboStatistics?
    private var nextFrameDeadline = 0.0
    private let frameInterval = 1.0 / 60.0
    private var lastInspection = 0.0
    private var measurementTime = 0.0
    private var measurementStep: UInt64 = 0
    private var performance = TurboPerformance()
    private var runAverage = RunThroughputAverage()
    private var automaticBatchInFlight = false
    private var estimate = 0.001
    private var batchSteps = 1
    private var awaitingIdleNotification = false
    private var palinstrophyRegulated = true
    private var reynoldsController = PalinstrophyReynoldsController()
    private var pendingCFL: Float?
    private var pendingReynolds: Double?
    var prepared: ((UInt64, String, String?, String?) -> Void)?
    var delivered: ((Delivery) -> Void)?
    var frameReady: ((UInt64, FastTurboFrame) -> Void)?
    var failed: ((UInt64, String) -> Void)?
    var settled: ((UInt64, Double) -> Void)?

    func configure(_ configuration: FastTurboConfiguration, palinstrophyRegulated: Bool, generation: UInt64,
                   until: Double?, targetWallSeconds: Double?, running: Bool, visible: Bool, includeVelocity: Bool,
                   initialField: URL?, spectrumOutput: URL?, fixedBatch: Int?) {
        queue.async {
            self.replacement = Run(configuration: configuration, palinstrophyRegulated: palinstrophyRegulated, generation: generation, until: until,
                                   targetWallSeconds: targetWallSeconds,
                                   initialField: initialField, spectrumOutput: spectrumOutput, fixedBatch: fixedBatch)
            self.pendingCFL = nil
            self.pendingReynolds = nil
            self.running = running
            self.visible = visible
            self.includeVelocity = includeVelocity
            self.pendingSteps = 0
            self.awaitingIdleNotification = true
            self.drive()
        }
    }

    func setPalinstrophyRegulated(_ enabled: Bool, manualReynolds: Double, generation: UInt64) {
        queue.async {
            guard (self.replacement?.generation ?? self.current?.generation) == generation else { return }
            self.palinstrophyRegulated = enabled
            self.reynoldsController.reset()
            if self.replacement != nil {
                self.replacement?.palinstrophyRegulated = enabled
                self.replacement?.configuration.reynolds = manualReynolds
            } else {
                self.pendingReynolds = manualReynolds
            }
            self.drive(publishWhenIdle: false)
        }
    }

    func setLiveParameters(cfl: Float?, reynolds: Double?, generation: UInt64) {
        queue.async {
            guard (self.replacement?.generation ?? self.current?.generation) == generation else { return }
            // While a batch runs, later drag events replace earlier values. If
            // a reset is already queued, update that run instead of the old core.
            if self.replacement != nil {
                if let cfl { self.replacement?.configuration.cfl = cfl }
                if let reynolds { self.replacement?.configuration.reynolds = reynolds }
            } else {
                if let cfl { self.pendingCFL = cfl }
                if let reynolds { self.pendingReynolds = reynolds }
            }
            self.drive(publishWhenIdle: false)
        }
    }

    func setRunning(_ value: Bool) {
        queue.async {
            if value && !self.running {
                // Restart only the rolling steps/s sample. Physical throughput
                // retains the accumulated history from every running segment.
                self.measurementTime = self.now
                self.measurementStep = self.statistics?.step ?? 0
            }
            self.running = value
            self.finished = false
            self.awaitingIdleNotification = true
            if !value { self.pendingSteps = 0 }
            if !value && !self.automaticBatchInFlight { self.stopClock(at: self.now) }
            self.drive()
        }
    }

    func setVisible(_ value: Bool) {
        queue.async {
            self.visible = value
            self.awaitingIdleNotification = true
            if value { self.needsFrame = true }
            self.drive()
        }
    }

    func singleStep() {
        queue.async {
            self.running = false
            self.finished = false
            self.pendingSteps += 1
            self.awaitingIdleNotification = true
            if !self.automaticBatchInFlight { self.stopClock(at: self.now) }
            self.drive()
        }
    }

    func requestFrame(includeVelocity: Bool) {
        queue.async {
            self.includeVelocity = includeVelocity
            self.needsFrame = true
            self.drive()
        }
    }

    private var now: Double { ProcessInfo.processInfo.systemUptime }

    private func wallTime(at time: Double) -> Double {
        runAverage.wallTime(at: time)
    }

    private func stopClock(at time: Double) {
        runAverage.stop(at: time)
    }

    private func drive(publishWhenIdle: Bool = true) {
        guard !busy else { return }
        if let next = replacement {
            replacement = nil
            current = next
            initialized = false
            finished = false
            finishedExported = false
            finalFrameReady = false
            statistics = nil
            performance = TurboPerformance()
            runAverage = RunThroughputAverage()
            automaticBatchInFlight = false
            palinstrophyRegulated = next.palinstrophyRegulated
            reynoldsController.reset()
            batchSteps = 1
            estimate = 0.001
            nextFrameDeadline = 0
            lastInspection = 0
            busy = true
            do {
                // Heavy Metal compilation, FFT planning, and state creation run
                // after the AppKit window is shown, entirely off the main thread.
                let input: [Float]?
                var initialSHA256: String?
                if let url = next.initialField {
                    let data = try Data(contentsOf: url)
                    let count = next.configuration.resolution * next.configuration.resolution
                    guard data.count == count * 4 else {
                        throw FastTurboError.invalidConfiguration("The initial field must contain exactly N² Float32 values.")
                    }
                    initialSHA256 = SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
                    input = data.withUnsafeBytes { bytes in
                        (0..<count).map { Float(bitPattern: bytes.loadUnaligned(fromByteOffset: $0 * 4, as: UInt32.self).littleEndian) }
                    }
                } else { input = nil }
                if let existing = core, existing.configuration == next.configuration {
                    try existing.setCFLSync(next.configuration.cfl)
                    try existing.setReynoldsSync(next.configuration.reynolds)
                    try existing.resetSync(vorticity: input)
                } else {
                    core = nil
                    core = try FastTurboCore(configuration: next.configuration, vorticity: input)
                }
                busy = false
                needsInspection = true
                needsFrame = true
                prepared?(next.generation, core!.device.name, next.initialField?.path, initialSHA256)
            } catch {
                busy = false
                running = false
                failed?(next.generation, error.localizedDescription)
                return
            }
        }
        guard let core, let current else { return }
        // drive() only reaches here after the previous GPU operation completed.
        // Applying scalar parameters cannot reset the flow, histories or clock.
        do {
            if let cfl = pendingCFL {
                try core.setCFLSync(cfl)
                pendingCFL = nil
            }
            if let reynolds = pendingReynolds {
                try core.setReynoldsSync(reynolds)
                pendingReynolds = nil
                statistics?.reynolds = reynolds
            }
        } catch { report(error); return }
        if needsInspection {
            needsInspection = false
            busy = true
            core.inspect { [weak self] result in
                guard let self else { return }
                self.queue.async {
                    self.busy = false
                    // A queued reset owns the latest run/pause intent. Results
                    // from the retired run must not change that intent.
                    if self.replacement != nil {
                        // Let this completion release its old core/frame before
                        // allocating the replacement configuration.
                        self.queue.async { self.drive() }
                        return
                    }
                    switch result {
                    case .success(let stats):
                        var stats = stats
                        if self.palinstrophyRegulated,
                           let reynolds = self.reynoldsController.nextReynolds(
                            current: stats.reynolds, metric: stats.palinstrophyOverEnstrophyKmaxSquared,
                            resolution: current.configuration.resolution,
                            initialWaveNumber: current.configuration.initialWaveNumber) {
                            do {
                                try core.setReynoldsSync(reynolds)
                                stats.reynolds = reynolds
                            } catch { self.report(error); return }
                        }
                        self.statistics = stats
                        self.lastInspection = self.now
                        if !self.initialized {
                            self.initialized = true
                            self.measurementTime = self.now
                            self.measurementStep = stats.step
                        }
                        if self.finished && !self.finishedExported {
                            self.finishedExported = true
                            if let url = current.spectrumOutput {
                                do {
                                    let values = try core.spectrumSync()
                                    let data = values.withUnsafeBytes { Data($0) }
                                    try data.write(to: url, options: .atomic)
                                } catch { self.report(error); return }
                            }
                        }
                        self.publish(force: true)
                    case .failure(let error): self.report(error)
                    }
                    self.drive()
                }
            }
            return
        }
        if visible, running || pendingSteps > 0,
           nextFrameDeadline > 0, now >= nextFrameDeadline { needsFrame = true }
        if needsFrame && visible {
            needsFrame = false
            busy = true
            core.snapshot(includeVelocity: includeVelocity) { [weak self] result in
                guard let self else { return }
                self.queue.async {
                    self.busy = false
                    if self.replacement != nil { self.queue.async { self.drive() }; return }
                    let completed = self.now
                    if self.nextFrameDeadline == 0 {
                        self.nextFrameDeadline = completed + self.frameInterval
                    } else if self.nextFrameDeadline <= completed {
                        // Keep the 60 Hz cadence anchored. Scheduling the next
                        // frame from this completion would accumulate batch jitter.
                        let missed = floor((completed - self.nextFrameDeadline) / self.frameInterval) + 1
                        self.nextFrameDeadline += missed * self.frameInterval
                    }
                    switch result {
                    case .success(let frame):
                        if let frame {
                            if self.finished { self.finalFrameReady = true }
                            self.frameReady?(current.generation, frame)
                        } else if !self.running {
                            // Leases can briefly occupy every slot while a PNG
                            // export finishes. Retry a paused display without spinning.
                            self.queue.asyncAfter(deadline: .now() + 1.0 / 60.0) {
                                guard self.current?.generation == current.generation else { return }
                                self.needsFrame = true
                                self.drive()
                            }
                        }
                    case .failure(let error): self.report(error)
                    }
                    self.drive()
                }
            }
            return
        }
        guard (running && !finished) || pendingSteps > 0 else {
            stopClock(at: now)
            // A paused scalar edit changes no telemetry except Re, which the
            // control already displays. Do not republish the entire dashboard.
            if publishWhenIdle { publish(force: true) }
            if awaitingIdleNotification {
                awaitingIdleNotification = false
                settled?(current.generation, now)
            }
            return
        }
        let single = pendingSteps > 0
        if single { pendingSteps -= 1 }
        var count = single ? 1 : (current.fixedBatch ?? batchSteps)
        if !single, current.fixedBatch == nil, visible, nextFrameDeadline > 0 {
            let available = max(0, nextFrameDeadline - now)
            count = min(count, max(1, Int(available / estimate)))
        }
        let started = now
        let initialSimulationTime = statistics?.time ?? 0
        runAverage.beginBatch(automatic: !single, at: started)
        automaticBatchInFlight = !single
        busy = true
        core.advance(steps: count, until: single ? nil : current.until) { [weak self] result in
            guard let self else { return }
            self.queue.async {
                self.busy = false
                self.automaticBatchInFlight = false
                if self.replacement != nil { self.queue.async { self.drive() }; return }
                let completed = self.now
                switch result {
                case .success(let stats):
                    self.statistics = stats
                    guard stats.isFinite else {
                        self.report(NSError(domain: "FastTurbo", code: 1,
                            userInfo: [NSLocalizedDescriptionKey: "The flow became non-finite. Reset the simulation and use a smaller CFL."]))
                        return
                    }
                    self.runAverage.completeBatch(from: initialSimulationTime, to: stats.time,
                                                  automatic: !single)
                    let sample = max((completed - started) / Double(count), 0.000001)
                    self.estimate = 0.8 * self.estimate + 0.2 * sample
                    let desired = max(1, min(32, Int(0.008 / self.estimate)))
                    self.batchSteps = min(desired, max(1, self.batchSteps * 2))
                    if completed - started > 0.016 {
                        self.batchSteps = max(1, min(self.batchSteps, Int(Double(count) * 0.008 / (completed - started))))
                    }
                    if !single {
                        let reachedTime = current.until.map { turboReachedEndpoint(stats.time, $0) } ?? false
                        // Check the active clock only after GPU completion. Warmup,
                        // reset, and output serialization are outside this interval.
                        let reachedWall = current.targetWallSeconds.map { self.wallTime(at: completed) >= $0 } ?? false
                        if reachedTime || reachedWall {
                            self.finished = true
                            self.running = false
                        }
                    }
                    if !self.running || self.finished { self.stopClock(at: completed) }
                    self.needsInspection = completed - self.lastInspection >= 0.25 || single || self.finished || !self.running
                    self.needsFrame = self.needsFrame || completed >= self.nextFrameDeadline || single || self.finished
                    if !self.needsInspection { self.publish(force: false) }
                case .failure(let error): self.report(error)
                }
                self.drive()
            }
        }
    }

    private func publish(force: Bool) {
        guard let stats = statistics, let current else { return }
        let time = now
        let interval = time - measurementTime
        guard force || interval >= 0.25 else { return }
        // Paused inspections and single steps retain the last live sample.
        if interval > 0 && running && !finished && stats.step > measurementStep {
            performance.simulationTimePerSecond = runAverage.rate(at: time)
            performance.stepsPerSecond = Double(stats.step >= measurementStep ? stats.step - measurementStep : 0) / interval
            performance.initialTurnoversPerSecond = stats.initialTurnoverTime > 0
                ? performance.simulationTimePerSecond / stats.initialTurnoverTime : 0
        }
        performance.millisecondsPerStep = estimate * 1000
        performance.batchSteps = current.fixedBatch ?? batchSteps
        delivered?(Delivery(statistics: stats, performance: performance,
                            elapsed: wallTime(at: time), generation: current.generation,
                            finished: finished && (!visible || finalFrameReady)))
        measurementTime = time
        measurementStep = stats.step
    }

    private func report(_ error: Error) {
        running = false
        pendingSteps = 0
        needsFrame = false
        needsInspection = false
        stopClock(at: now)
        failed?(current?.generation ?? 0, error.localizedDescription)
    }
}

@MainActor
final class SimulationController: ObservableObject {
    private static let startupConfiguration: FastTurboConfiguration = {
        var configuration = FastTurboConfiguration()
        configuration.resolution = 2048
        configuration.initialWaveNumber = 10
        configuration.reynolds = FastTurboConfiguration.reynoldsFromNAndK0(
            configuration.resolution, configuration.initialWaveNumber)
        return configuration
    }()

    var configuration = SimulationController.startupConfiguration {
        willSet {
            var structural = newValue
            structural.cfl = configuration.cfl
            structural.reynolds = configuration.reynolds
            if structural != configuration { objectWillChange.send() }
        }
        didSet {
            let gridChanged = configuration.resolution != oldValue.resolution
                || configuration.initialWaveNumber != oldValue.initialWaveNumber
            if gridChanged && isPalinstrophyRegulated {
                let initial = FastTurboConfiguration.reynoldsFromNAndK0(
                    configuration.resolution, configuration.initialWaveNumber)
                manualReynolds = initial
                effectiveReynolds = initial
                configuration.reynolds = initial
            }
            if !isPalinstrophyRegulated,
               configuration.reynolds != oldValue.reynolds {
                manualReynolds = configuration.reynolds
                effectiveReynolds = configuration.reynolds
            }
            updateLiveControlDisplay()
            guard hasStarted, configuration != oldValue else { return }
            var structuralConfiguration = configuration
            structuralConfiguration.reynolds = oldValue.reynolds
            structuralConfiguration.cfl = oldValue.cfl
            if structuralConfiguration == oldValue {
                scheduleLiveParameters(cfl: configuration.cfl != oldValue.cfl ? configuration.cfl : nil,
                                       reynolds: configuration.reynolds != oldValue.reynolds ? configuration.reynolds : nil)
            } else {
                reset()
            }
        }
    }
    /// The slider's value in manual mode. Automatic mode displays the effective
    /// value here but does not let a drag alter the running PID controller.
    private(set) var manualReynolds = SimulationController.startupConfiguration.reynolds
    private(set) var effectiveReynolds = SimulationController.startupConfiguration.reynolds
    var isPalinstrophyRegulated = true {
        didSet {
            guard isPalinstrophyRegulated != oldValue else { return }
            if !isPalinstrophyRegulated {
                manualReynolds = effectiveReynolds
                configuration.reynolds = manualReynolds
            }
            updateLiveControlDisplay()
            guard hasStarted else { return }
            flushLiveParameters()
            pump.setPalinstrophyRegulated(isPalinstrophyRegulated, manualReynolds: manualReynolds,
                                         generation: generation)
        }
    }
    @Published var display: TurboField = .vorticity { didSet { refreshField() } }
    @Published var palette: TurboPalette = .ember
    @Published var exposure = 1.2
    @Published var vorticitySigma = 2.5
    @Published var vorticityColorRange: TurboColorRange = .sigma
    @Published var showFlowLines = false { didSet { refreshField() } }
    @Published var isFocusMode = false
    @Published private(set) var isRunning = true
    @Published private(set) var isReady = false
    @Published private(set) var isSettled = false
    @Published private(set) var statistics: FastTurboStatistics?
    @Published private(set) var performance = TurboPerformance()
    @Published private(set) var history: [Double] = []
    @Published private(set) var deviceName = "Apple Metal"
    @Published var statusMessage: String?
    @Published private(set) var isExporting = false
    let frames = DisplayFrameSource()
    let liveControls = LiveControlDisplay()
    private let pump = SimulationPump()
    private var liveParameterUpdate: DispatchWorkItem?
    private var pendingCFL: Float?
    private var pendingReynolds: Double?
    private var hasStarted = false
    private var visible = true
    private var finishedGeneration: UInt64?
    private(set) var generation: UInt64 = 0
    private(set) var elapsedRunSeconds = 0.0
    private(set) var lastIdleWallTime = 0.0
    private(set) var presentationTimes: [Double] = []
    private(set) var totalPresentations: UInt64 = 0
    private(set) var loadedInitialFile: String?
    private(set) var loadedInitialSHA256: String?
    var targetTime: Double?
    var targetWallSeconds: Double?
    var initialFieldURL: URL?
    var spectrumOutputURL: URL?
    var fixedBatchSteps: Int?
    var onRunFinished: (() -> Void)?

    var time: Double { statistics?.time ?? 0 }
    var step: UInt64 { statistics?.step ?? 0 }
    var timeStep: Double { Double(statistics?.timeStep ?? 0) }
    var energy: Double { statistics?.energy ?? 0 }
    var enstrophy: Double { statistics?.enstrophy ?? 0 }
    var initialTurnoverTime: Double { statistics?.initialTurnoverTime ?? 0 }
    var allocatedBytes: Int { frames.frame?.buffer.device.currentAllocatedSize ?? statistics?.allocatedBytes ?? 0 }
    var palinstrophyControlValue: Double { 10_000 * (statistics?.palinstrophyOverEnstrophyKmaxSquared ?? 0) }
    var logarithmicReynolds: Double { log10(isPalinstrophyRegulated ? effectiveReynolds : manualReynolds) }

    init() {
        pump.prepared = { [weak self] generation, name, initialFile, initialSHA256 in
            DispatchQueue.main.async {
                guard let self, self.generation == generation else { return }
                self.deviceName = name
                self.loadedInitialFile = initialFile
                self.loadedInitialSHA256 = initialSHA256
            }
        }
        pump.delivered = { [weak self] result in
            DispatchQueue.main.async {
                guard let self, self.generation == result.generation else { return }
                self.statistics = result.statistics
                // An older batch completion must not move the manual readout
                // back after the user has already selected a newer value.
                self.effectiveReynolds = self.isPalinstrophyRegulated ? result.statistics.reynolds : self.manualReynolds
                self.updateLiveControlDisplay()
                var performance = result.performance
                if !self.isRunning {
                    // A delivery already queued when Pause was pressed must
                    // not replace the values that were visible at that moment.
                    performance.simulationTimePerSecond = self.performance.simulationTimePerSecond
                    performance.initialTurnoversPerSecond = self.performance.initialTurnoversPerSecond
                    performance.stepsPerSecond = self.performance.stepsPerSecond
                }
                self.performance = performance
                self.elapsedRunSeconds = result.elapsed
                if self.history.last != result.statistics.energy {
                    self.history.append(result.statistics.energy)
                    if self.history.count > 120 { self.history.removeFirst(self.history.count - 120) }
                }
                if result.finished && self.finishedGeneration != result.generation {
                    self.finishedGeneration = result.generation
                    self.isRunning = false
                    self.onRunFinished?()
                }
            }
        }
        pump.frameReady = { [weak self] generation, frame in
            DispatchQueue.main.async {
                guard let self, self.generation == generation else { return }
                self.frames.frame = frame
                if !self.isReady { self.isReady = true }
            }
        }
        pump.failed = { [weak self] generation, message in
            DispatchQueue.main.async {
                guard let self, self.generation == generation else { return }
                self.statusMessage = message
                self.isRunning = false
                self.isSettled = true
            }
        }
        pump.settled = { [weak self] generation, time in
            DispatchQueue.main.async {
                guard let self, self.generation == generation else { return }
                self.isSettled = true
                self.lastIdleWallTime = time
            }
        }
    }

    func start() {
        guard !hasStarted else { setVisible(true); return }
        hasStarted = true
        reset()
    }

    func reset() {
        // The replacement configuration already contains the latest selection.
        // A delayed drag update must never leak into a later generation.
        liveParameterUpdate?.cancel()
        liveParameterUpdate = nil
        pendingCFL = nil
        pendingReynolds = nil
        generation &+= 1
        finishedGeneration = nil
        loadedInitialFile = nil
        loadedInitialSHA256 = nil
        isReady = false
        isSettled = false
        statusMessage = nil
        statistics = nil
        updateLiveControlDisplay()
        performance = TurboPerformance()
        history.removeAll(keepingCapacity: true)
        frames.generation = generation
        frames.frame = nil
        elapsedRunSeconds = 0
        presentationTimes.removeAll(keepingCapacity: true)
        totalPresentations = 0
        pump.configure(configuration, palinstrophyRegulated: isPalinstrophyRegulated, generation: generation, until: targetTime,
                       targetWallSeconds: targetWallSeconds,
                       running: isRunning, visible: visible, includeVelocity: display == .speed || showFlowLines,
                       initialField: initialFieldURL, spectrumOutput: spectrumOutputURL, fixedBatch: fixedBatchSteps)
    }

    func newSeed() { configuration.seed &+= 1 }

    func setLogarithmicReynolds(_ value: Double) {
        guard !isPalinstrophyRegulated, value.isFinite else { return }
        let reynolds = min(PalinstrophyReynoldsController.maximumReynolds,
                           max(PalinstrophyReynoldsController.minimumReynolds, pow(10, value)))
        guard reynolds != manualReynolds else { return }
        configuration.reynolds = reynolds
    }

    func setCFL(_ value: Double) {
        guard value.isFinite else { return }
        let cfl = Float(min(3, max(1, value)))
        if cfl != configuration.cfl { configuration.cfl = cfl }
    }

    private func updateLiveControlDisplay() {
        liveControls.update(cfl: configuration.cfl, reynolds: effectiveReynolds,
                            regulated: isPalinstrophyRegulated, palinstrophy: palinstrophyControlValue)
    }

    private func scheduleLiveParameters(cfl: Float?, reynolds: Double?) {
        if let cfl { pendingCFL = cfl }
        if let reynolds { pendingReynolds = reynolds }
        guard liveParameterUpdate == nil else { return }
        // The control follows every drag event locally. Send only the latest
        // pair to the solver at 30 Hz, without creating a queue item per event.
        let update = DispatchWorkItem { [weak self] in self?.flushLiveParameters() }
        liveParameterUpdate = update
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.0 / 30.0, execute: update)
    }

    /// Commit the final drag value immediately, also before a transport or mode
    /// change so a single step cannot run with an older slider selection.
    func flushLiveParameters() {
        liveParameterUpdate?.cancel()
        liveParameterUpdate = nil
        guard pendingCFL != nil || pendingReynolds != nil else { return }
        pump.setLiveParameters(cfl: pendingCFL, reynolds: pendingReynolds, generation: generation)
        pendingCFL = nil
        pendingReynolds = nil
    }

    func togglePause() { setRunning(!isRunning) }

    func setRunning(_ value: Bool) {
        flushLiveParameters()
        isRunning = value
        isSettled = false
        pump.setRunning(value)
    }

    func singleStep() {
        flushLiveParameters()
        isRunning = false
        isSettled = false
        pump.singleStep()
    }

    func setVisible(_ value: Bool) {
        visible = value
        isSettled = false
        pump.setVisible(value)
    }

    private func refreshField() {
        guard hasStarted else { return }
        pump.requestFrame(includeVelocity: display == .speed || showFlowLines)
    }

    func recordPresentation(_ frameGeneration: UInt64, _ time: Double) {
        guard frameGeneration == generation else { return }
        totalPresentations &+= 1
        presentationTimes.append(time)
        // A bounded diagnostic ring has no growing allocation during long runs.
        if presentationTimes.count > 36_000 { presentationTimes.removeFirst(18_000) }
    }

    func saveSnapshot() {
        guard let frame = frames.frame, !isExporting else { return }
        let panel = NSSavePanel()
        panel.allowedContentTypes = [.png]
        panel.canCreateDirectories = true
        panel.nameFieldStringValue = String(format: "2DFastTurbo-%06llu.png", frame.sequence)
        guard panel.runModal() == .OK, let url = panel.url else { return }
        isExporting = true
        let palette = palette, display = display, exposure = exposure, flow = showFlowLines
        let vorticitySigma = vorticitySigma, vorticityColorRange = vorticityColorRange
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            do {
                let data = try TurboSnapshot.pngData(frame: frame, palette: palette, display: display,
                                                     exposure: exposure, vorticitySigma: vorticitySigma,
                                                     vorticityColorRange: vorticityColorRange, showFlowLines: flow)
                try data.write(to: url, options: .atomic)
                DispatchQueue.main.async { self?.isExporting = false }
            } catch {
                let message = error.localizedDescription
                DispatchQueue.main.async {
                    self?.isExporting = false
                    self?.statusMessage = "Could not save the snapshot: \(message)"
                }
            }
        }
    }
}
