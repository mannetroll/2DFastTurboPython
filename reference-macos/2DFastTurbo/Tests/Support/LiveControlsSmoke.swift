import Combine
import Foundation

/// Compile with production core/app Swift sources (except AppMain/BenchMain)
/// and the two bridge objects. Exercises the actual model and GPU pump.
@main
enum LiveControlsSmoke {
    @MainActor
    static func wait(_ reason: String, until condition: () -> Bool) async throws {
        let deadline = ProcessInfo.processInfo.systemUptime + 15
        while !condition() {
            guard ProcessInfo.processInfo.systemUptime < deadline else {
                throw NSError(domain: "LiveControlsSmoke", code: 1,
                              userInfo: [NSLocalizedDescriptionKey: reason])
            }
            try await Task.sleep(nanoseconds: 5_000_000)
        }
    }

    @MainActor
    static func main() async throws {
        let model = SimulationController()
        model.isPalinstrophyRegulated = false
        var configuration = model.configuration
        model.configuration.resolution = 4096
        precondition(model.effectiveReynolds == FastTurboConfiguration.reynoldsFromNAndK0(4096, 10))
        model.configuration.initialWaveNumber = 8
        precondition(model.effectiveReynolds == FastTurboConfiguration.reynoldsFromNAndK0(4096, 8))
        precondition(!model.isPalinstrophyRegulated)
        configuration.resolution = 32
        configuration.reynolds = 10_000
        model.configuration = configuration
        precondition(model.effectiveReynolds == 10_000, "An explicit manual Re must be preserved")
        model.setRunning(false)
        model.start()
        try await wait("Initial paused frame") { model.isReady && model.isSettled }
        model.singleStep()
        try await wait("First single step") { model.isSettled && model.step == 1 }
        let generation = model.generation, time = model.time
        let frame = model.frames.frame
        var windowPublications = 0
        var controlPublications = 0
        let windowSubscription = model.objectWillChange.sink { windowPublications += 1 }
        let controlSubscription = model.liveControls.objectWillChange.sink { controlPublications += 1 }

        // Drag both sliders repeatedly while paused. Nothing should reset, and
        // completed statistics cannot overwrite the latest manual readout.
        for tick in 0...100 {
            model.configuration.cfl = Float(1 + 2 * Double(tick) / 100)
            model.setLogarithmicReynolds(3 + Double(tick) / 100)
            precondition(model.liveControls.values.cfl == model.configuration.cfl)
            precondition(model.liveControls.values.reynolds == Int(model.manualReynolds.rounded()))
        }
        try await Task.sleep(nanoseconds: 100_000_000)
        precondition(windowPublications == 0, "Paused sliders must not invalidate the whole window")
        precondition(controlPublications > 0, "The isolated readout must follow the drag immediately")
        precondition(model.generation == generation && model.time == time && model.step == 1)
        precondition(model.frames.frame === frame)
        precondition(abs(model.effectiveReynolds - 10_000) < 1e-8)

        // Duplicate events publish nothing, and release/single-step flushes the
        // last pending selection without waiting for the 30 Hz delivery timer.
        let unchangedPublications = controlPublications
        model.setCFL(3)
        model.setLogarithmicReynolds(4)
        precondition(controlPublications == unchangedPublications)
        model.setCFL(1)
        model.setLogarithmicReynolds(3)
        model.flushLiveParameters()
        model.singleStep()
        try await wait("Apply both controls before single step") { model.isSettled && model.step == 2 }
        precondition(model.statistics?.reynolds == 1_000)
        precondition(model.generation == generation && model.time > time)

        model.isPalinstrophyRegulated = true
        precondition(model.liveControls.values.isPalinstrophyRegulated)
        model.configuration.cfl = 2
        model.singleStep()
        try await wait("CFL in regulated mode") { model.isSettled && model.step == 3 }
        precondition(model.generation == generation)
        model.isPalinstrophyRegulated = false
        precondition(!model.liveControls.values.isPalinstrophyRegulated)
        let heldReynolds = model.manualReynolds
        precondition(model.configuration.reynolds == heldReynolds)
        precondition(model.generation == generation)

        model.setRunning(true)
        try await wait("Running flow") { model.step > 3 }
        let runningTime = model.time, runningStep = model.step
        for tick in 0...100 {
            model.configuration.cfl = Float(1 + Double(tick) / 100)
            model.setLogarithmicReynolds(3 + Double(tick) / 100)
        }
        try await wait("Running parameter change") { model.step > runningStep && model.statistics?.reynolds == 10_000 }
        precondition(model.generation == generation && model.time > runningTime)
        model.setRunning(false)
        try await wait("Pause drains current batch") { model.isSettled }

        // A structural reset queued during work must receive the latest slider
        // values rather than applying them to the retiring solver.
        model.setRunning(true)
        model.configuration.resolution = 34
        let nextGeneration = model.generation
        model.setLogarithmicReynolds(3)
        model.configuration.cfl = 1
        model.setRunning(false)
        try await wait("Replacement receives live settings") { model.isReady && model.isSettled && model.frames.frame?.gridSize == 34 }
        precondition(nextGeneration == generation + 1 && model.generation == nextGeneration)
        precondition(model.statistics?.reynolds == 1_000)

        // Auto-mode grid changes must still perform the structural reset after
        // recalculating the initial Reynolds number.
        model.isPalinstrophyRegulated = true
        model.configuration.resolution = 32
        try await wait("Regulated grid reset") { model.isReady && model.isSettled && model.frames.frame?.gridSize == 32 }
        precondition(model.generation == nextGeneration + 1)
        precondition(model.statusMessage == nil)
        withExtendedLifetime((windowSubscription, controlSubscription)) {}
        print("PASS: isolated CFL/Re updates, no duplicate publications, paused/running drag, mode switches, release/single-step, frame/clock preservation, queued reset")
    }
}
