import AppKit
import Foundation
import MetalKit

/// Opt-in checks exercise the real window, worker, renderer and controls. Normal
/// launches have no timer or diagnostic capture beyond bounded presentation times.
@MainActor
final class FastTurboGUIDiagnostics {
    private weak var application: FastTurboAppDelegate?
    private var model: SimulationController { application!.model }
    private let benchmark: Bool
    private var timer: Timer?
    private var stage: Stage = .ready
    private var deadline = 0.0
    private var warmupSeconds = 2.0
    private var target = 10.0
    private var targetWallSeconds: Double?
    private var output: URL?
    private var spectrum: URL?
    private var exportImage: URL?
    private var launchSeconds = 0.0
    private var warmStarted = 0.0
    private var pauseStarted = 0.0
    private var pausedStep: UInt64 = 0
    private var pausedThroughput: [String: Double] = [:]
    private var originalWindowFrame: NSRect?
    private var originalCanvasSide: CGFloat = 0
    private var focusCanvasSide: CGFloat = 0
    private var layoutChangedAt = 0.0
    private var reopenStarted = 0.0
    private var reopened = false
    private var initialEnergy = 0.0
    private var initialEnstrophy = 0.0
    private var savedConfiguration: FastTurboConfiguration?
    private var checks: [String: Any] = [:]
    private enum Stage {
        case ready, warming, resetRunning, reconfigurePausing, restoreConfiguration, resumeReconfigured
        case layoutWidth, layoutHeight, layoutFocus, layoutControls, layoutRestored
        case pausing, holdingPause, singleStep, resetPaused, reopening, measuring, done
    }

    init?(application: FastTurboAppDelegate) {
        let args = CommandLine.arguments
        guard args.contains("--gui-benchmark") || args.contains("--live-check") else { return nil }
        self.application = application
        benchmark = args.contains("--gui-benchmark")
    }

    private func argument(_ name: String) -> String? {
        let args = CommandLine.arguments
        guard let index = args.firstIndex(of: name), index + 1 < args.count else { return nil }
        return args[index + 1]
    }

    func configureBeforeStart() {
        if let path = argument("--output") { output = URL(fileURLWithPath: path) }
        if let path = argument("--spectrum") { spectrum = URL(fileURLWithPath: path) }
        if let path = argument("--export") { exportImage = URL(fileURLWithPath: path) }
        var configuration = model.configuration
        if let value = argument("--resolution").flatMap(Int.init) { configuration.resolution = value }
        if let value = argument("--reynolds").flatMap(Double.init) {
            configuration.reynolds = value
            // Benchmark and automated GUI invocations request a fixed workload.
            model.isPalinstrophyRegulated = false
        }
        if let value = argument("--k0").flatMap(Int.init) { configuration.initialWaveNumber = value }
        if let value = argument("--seed").flatMap(UInt64.init) { configuration.seed = value }
        if let value = argument("--cfl").flatMap(Float.init) { configuration.cfl = value }
        if let name = argument("--integrator") {
            guard let integrator = Integrator(rawValue: name) else { fail("Unknown integrator: \(name)"); return }
            configuration.integrator = integrator
        }
        if let name = argument("--operator") {
            guard let formulation = NonlinearFormulation(rawValue: name) else { fail("Unknown nonlinear operator: \(name)"); return }
            configuration.nonlinearFormulation = formulation
        }
        if let value = (argument("--fixed-dt") ?? argument("--dt")).flatMap(Float.init) { configuration.fixedTimeStep = value }
        let hasPhysicalTarget = CommandLine.arguments.contains("--until")
        let hasWallTarget = CommandLine.arguments.contains("--wall-seconds")
        guard !(hasPhysicalTarget && hasWallTarget) else {
            fail("Specify only one of --until and --wall-seconds."); return
        }
        if hasPhysicalTarget {
            guard let value = argument("--until").flatMap(Double.init), value.isFinite, value > 0 else {
                fail("GUI target time must be finite and positive."); return
            }
            target = value
        }
        if hasWallTarget {
            guard benchmark else { fail("--wall-seconds requires --gui-benchmark."); return }
            guard let value = argument("--wall-seconds").flatMap(Double.init), value.isFinite, value > 0 else {
                fail("GUI wall duration must be finite and positive."); return
            }
            targetWallSeconds = value
        }
        if let value = argument("--gui-warmup-seconds").flatMap(Double.init) { warmupSeconds = value }
        guard target.isFinite, target > 0, warmupSeconds.isFinite, warmupSeconds >= 0 else {
            fail("GUI target time must be positive; warmup must be nonnegative."); return
        }
        if let text = argument("--batch") {
            guard let batch = Int(text), (1...32).contains(batch) else { fail("GUI batch must be between 1 and 32."); return }
            model.fixedBatchSteps = batch
        }
        if let path = argument("--initial") { model.initialFieldURL = URL(fileURLWithPath: path) }
        model.configuration = configuration
        if let name = argument("--display") {
            guard let field = TurboField(rawValue: name) else { fail("Unknown display field: \(name)"); return }
            model.display = field
        } else { model.display = .enstrophy }
        if let name = argument("--palette") {
            guard let palette = TurboPalette(rawValue: name) else { fail("Unknown palette: \(name)"); return }
            model.palette = palette
        }
        if let text = argument("--exposure") {
            guard let exposure = Double(text), exposure.isFinite, exposure > 0 else {
                fail("Exposure must be finite and positive."); return
            }
            model.exposure = exposure
        }
        if let name = argument("--color-range") {
            guard let range = TurboColorRange(rawValue: name) else {
                fail("Color range must be sigma or exposure."); return
            }
            model.vorticityColorRange = range
        }
        if let text = argument("--sigma") {
            guard let sigma = Double(text), sigma.isFinite, (1.5...3.5).contains(sigma) else {
                fail("Sigma range must be between 1.5 and 3.5."); return
            }
            model.vorticitySigma = sigma
        }
        model.showFlowLines = false
        model.targetTime = nil
        model.targetWallSeconds = nil
        configureCanvasSize()
    }

    private func configureCanvasSize() {
        if let text = argument("--canvas-size") {
            guard let side = Double(text), side.isFinite, side >= Double(TurboLayout.minimumCanvasSide),
                  let application, let window = application.windowController?.window else {
                fail("Invalid benchmark canvas size."); return
            }
            let width = CGFloat(side) + TurboLayout.widthAroundCanvas(focus: false)
                + window.frame.width - window.contentLayoutRect.width
            let size = application.windowWillResize(window, to: NSSize(width: width, height: window.frame.height))
            window.setFrame(NSRect(x: window.frame.minX, y: window.frame.maxY - size.height,
                                   width: size.width, height: size.height), display: true)
        }
    }

    func start(launchSeconds: Double) {
        self.launchSeconds = launchSeconds
        deadline = now + 120
        let timer = Timer(timeInterval: 0.02, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.tick() }
        }
        RunLoop.main.add(timer, forMode: .common)
        self.timer = timer
        model.onRunFinished = { [weak self] in self?.finishBenchmark() }
    }

    private var now: Double { ProcessInfo.processInfo.systemUptime }
    private var physicalTarget: Double? { targetWallSeconds == nil ? target : nil }

    private func tick() {
        guard stage != .done else { return }
        if let error = model.statusMessage { fail(error); return }
        guard now < deadline else { fail("Timed out during GUI check: \(stage)"); return }
        switch stage {
        case .ready:
            guard model.isReady, model.step > 0, model.totalPresentations > 0,
                  application?.windowController?.window?.isVisible == true else { return }
            checks["window_visible_on_first_launch"] = true
            checks["first_frame_presented"] = true
            if let requested = argument("--canvas-size").flatMap(Double.init) {
                guard let side = verifySquareCanvas("benchmark_viewport") else { return }
                guard abs(Double(side) - requested) <= 1 else {
                    fail("The requested benchmark canvas does not fit on this screen."); return
                }
            }
            if benchmark { beginWarmup() }
            else { beginLayoutCheck() }
        case .layoutWidth:
            guard now - layoutChangedAt >= 0.15 else { return }
            guard verifySquareCanvas("width_resize") != nil else { return }
            resizeForLayoutCheck(widthDriven: false)
            stage = .layoutHeight
        case .layoutHeight:
            guard now - layoutChangedAt >= 0.15 else { return }
            guard let side = verifySquareCanvas("height_resize") else { return }
            focusCanvasSide = side
            model.isFocusMode = true
            layoutChangedAt = now
            stage = .layoutFocus
        case .layoutFocus:
            guard now - layoutChangedAt >= 0.15 else { return }
            guard verifySquareCanvas("focus_mode", retainedSide: focusCanvasSide) != nil else { return }
            model.isFocusMode = false
            layoutChangedAt = now
            stage = .layoutControls
        case .layoutControls:
            guard now - layoutChangedAt >= 0.15 else { return }
            guard verifySquareCanvas("controls_restored", retainedSide: focusCanvasSide) != nil else { return }
            if let originalWindowFrame {
                application?.windowController?.window?.setFrame(originalWindowFrame, display: true)
            }
            layoutChangedAt = now
            stage = .layoutRestored
        case .layoutRestored:
            guard now - layoutChangedAt >= 0.15 else { return }
            guard verifySquareCanvas("original_frame_restored", retainedSide: originalCanvasSide) != nil else { return }
            originalWindowFrame = nil
            beginWarmup()
        case .warming:
            guard now - warmStarted >= warmupSeconds else { return }
            if benchmark { beginPauseCheck() }
            else {
                model.reset()
                stage = .resetRunning
                deadline = now + 120
            }
        case .resetRunning:
            guard model.isReady, model.step > 0 else { return }
            checks["reset_while_running"] = true
            savedConfiguration = model.configuration
            var altered = model.configuration
            altered.seed ^= 1
            model.configuration = altered
            model.setRunning(false)
            stage = .reconfigurePausing
            deadline = now + 120
        case .reconfigurePausing:
            guard model.isReady, model.isSettled else { return }
            guard model.step == 0, model.time == 0, !model.isRunning else {
                fail("Reconfiguration did not preserve the latest pause request."); return
            }
            checks["reconfigure_preserved_pause"] = true
            if let savedConfiguration { model.configuration = savedConfiguration }
            stage = .restoreConfiguration
            deadline = now + 120
        case .restoreConfiguration:
            guard model.isReady, model.isSettled, model.step == 0 else { return }
            model.setRunning(true)
            stage = .resumeReconfigured
            deadline = now + 20
        case .resumeReconfigured:
            guard model.isReady, model.step > 0, hasMeasuredThroughput else { return }
            checks["reconfigure_resume"] = true
            beginPauseCheck()
        case .pausing:
            guard model.isSettled else { return }
            guard verifyRetainedThroughput("settled_pause") else { return }
            checks["pause_ms"] = max(0, model.lastIdleWallTime - pauseStarted) * 1000
            pausedStep = model.step
            pauseStarted = now
            stage = .holdingPause
        case .holdingPause:
            guard now - pauseStarted >= 0.15 else { return }
            guard model.step == pausedStep else { fail("The paused solver kept advancing."); return }
            guard verifyRetainedThroughput("held_pause") else { return }
            checks["pause_stable"] = true
            model.singleStep()
            guard verifyRetainedThroughput("immediate_single_step") else { return }
            stage = .singleStep
            deadline = now + 20
        case .singleStep:
            guard model.isSettled, model.step > pausedStep else { return }
            let difference = model.step - pausedStep
            checks["single_step_delta"] = difference
            guard difference == 1 else { fail("Single Step advanced \(difference) steps."); return }
            guard verifyRetainedThroughput("single_step") else { return }
            model.reset()
            guard verifyClearedThroughput("immediate_reset") else { return }
            stage = .resetPaused
            deadline = now + 120
        case .resetPaused:
            guard model.isReady, model.isSettled else { return }
            guard model.step == 0, model.time == 0 else { fail("Reset did not return to t = 0."); return }
            guard verifyClearedThroughput("settled_reset") else { return }
            checks["reset_at_zero"] = true
            initialEnergy = model.energy
            initialEnstrophy = model.enstrophy
            application?.windowController?.window?.performClose(nil)
            reopenStarted = now
            reopened = false
            stage = .reopening
            deadline = now + 15
        case .reopening:
            guard now - reopenStarted >= 0.15 else { return }
            if !reopened {
                _ = application?.applicationShouldHandleReopen(NSApp, hasVisibleWindows: false)
                // Rehosting the closed SwiftUI view can change the window's
                // content size. Restore the common benchmark viewport before
                // collecting either timing or presentation measurements.
                if benchmark { configureCanvasSize() }
                reopened = true
                return
            }
            guard application?.windowController?.window?.isVisible == true,
                  application?.windowController?.window?.isMiniaturized == false,
                  application?.windowController?.window?.occlusionState.contains(.visible) == true,
                  !NSApp.isHidden,
                  model.isSettled, model.totalPresentations > 0 else { return }
            checks["reopen_visible"] = true
            if benchmark {
                if let requested = argument("--canvas-size").flatMap(Double.init) {
                    guard let side = verifySquareCanvas("measured_viewport"),
                          abs(Double(side) - requested) <= 1 else {
                        fail("The reopened benchmark canvas differs from the requested size."); return
                    }
                }
                model.targetTime = physicalTarget
                model.targetWallSeconds = targetWallSeconds
                model.spectrumOutputURL = spectrum
                model.setRunning(true)
                model.reset()
                stage = .measuring
                deadline = now + max(600, (targetWallSeconds ?? 0) + 120)
            } else { finishLiveCheck() }
        case .measuring:
            let reachedTarget = targetWallSeconds.map { model.elapsedRunSeconds >= $0 } ?? (model.time >= target)
            guard !model.isReady || model.isRunning || reachedTarget else {
                fail("The live benchmark was paused before reaching its endpoint."); return
            }
            // Completion comes from the worker after the final frame lease has
            // been delivered, never from a separately polled time counter.
            guard let window = application?.windowController?.window,
                  window.isVisible, !window.isMiniaturized, !NSApp.isHidden,
                  window.occlusionState.contains(.visible) else {
                fail("The live benchmark window became hidden or fully occluded."); return
            }
        case .done: break
        }
    }

    private func beginWarmup() {
        warmStarted = now
        stage = .warming
        deadline = now + max(20, warmupSeconds + 15)
    }

    private func beginLayoutCheck() {
        guard let window = application?.windowController?.window,
              let side = verifySquareCanvas("initial_window"), verifySpaceShortcut(window) else { return }
        originalWindowFrame = window.frame
        originalCanvasSide = side
        resizeForLayoutCheck(widthDriven: true)
        stage = .layoutWidth
        deadline = now + 20
    }

    private func resizeForLayoutCheck(widthDriven: Bool) {
        guard let application, let window = application.windowController?.window else { return }
        var proposed = window.frame.size
        if widthDriven { proposed.width += 48 }
        else { proposed.height -= 48 }
        let size = application.windowWillResize(window, to: proposed)
        let frame = NSRect(x: window.frame.minX, y: window.frame.maxY - size.height,
                           width: size.width, height: size.height)
        window.setFrame(frame, display: true)
        layoutChangedAt = now
    }

    private func metalView(in view: NSView) -> MTKView? {
        if let canvas = view as? MTKView { return canvas }
        for child in view.subviews {
            if let canvas = metalView(in: child) { return canvas }
        }
        return nil
    }

    private func verifySquareCanvas(_ phase: String, retainedSide: CGFloat? = nil) -> CGFloat? {
        guard let window = application?.windowController?.window, let content = window.contentView else {
            fail("No window content during \(phase).")
            return nil
        }
        content.layoutSubtreeIfNeeded()
        guard let canvas = metalView(in: content) else {
            fail("No Metal canvas during \(phase).")
            return nil
        }
        let actual = canvas.convert(canvas.bounds, to: nil)
        let layout = window.contentLayoutRect
        let expected = NSRect(x: layout.minX + TurboLayout.horizontalPadding,
                              y: layout.minY + TurboLayout.bottomPadding
                                + (model.isFocusMode ? 0 : TurboLayout.statsHeight + TurboLayout.statsSpacing),
                              width: layout.width - TurboLayout.widthAroundCanvas(focus: model.isFocusMode),
                              height: layout.height - TurboLayout.heightAroundCanvas(focus: model.isFocusMode))
        let square = actual.width > 0 && abs(actual.width - actual.height) <= 1
        let fillsArea = abs(actual.minX - expected.minX) <= 1 && abs(actual.minY - expected.minY) <= 1
            && abs(actual.width - expected.width) <= 1 && abs(actual.height - expected.height) <= 1
        let sideRetained = retainedSide.map { abs(actual.width - $0) <= 1 } ?? true
        checks["layout_\(phase)"] = [
            "canvas_points": [actual.minX, actual.minY, actual.width, actual.height].map(Double.init),
            "expected_canvas_points": [expected.minX, expected.minY, expected.width, expected.height].map(Double.init),
            "square": square, "fills_canvas_area": fillsArea, "canvas_side_retained": sideRetained
        ] as [String: Any]
        guard square, fillsArea, sideRetained else {
            fail("Canvas layout failed during \(phase): square=\(square), fills area=\(fillsArea), retained side=\(sideRetained).")
            return nil
        }
        return actual.width
    }

    private func verifySpaceShortcut(_ window: NSWindow) -> Bool {
        guard let press = NSEvent.keyEvent(with: .keyDown, location: .zero, modifierFlags: [],
                                           timestamp: now, windowNumber: window.windowNumber, context: nil,
                                           characters: " ", charactersIgnoringModifiers: " ",
                                           isARepeat: false, keyCode: 49),
              let repeated = NSEvent.keyEvent(with: .keyDown, location: .zero, modifierFlags: [],
                                              timestamp: now, windowNumber: window.windowNumber, context: nil,
                                              characters: " ", charactersIgnoringModifiers: " ",
                                              isARepeat: true, keyCode: 49) else {
            fail("Could not create Space events for the transport check.")
            return false
        }
        let wasRunning = model.isRunning
        window.sendEvent(press)
        let toggled = model.isRunning != wasRunning
        window.sendEvent(repeated)
        let repeatIgnored = model.isRunning != wasRunning
        let handled = window.performKeyEquivalent(with: press)
        let resumed = model.isRunning == wasRunning
        checks["space_toggles_transport"] = toggled
        checks["space_repeat_does_not_toggle"] = repeatIgnored
        checks["space_key_equivalent_restores_transport"] = handled && resumed
        guard toggled, repeatIgnored, handled, resumed else {
            fail("Space did not reliably toggle transport through the window event paths.")
            return false
        }
        return true
    }

    private func beginPauseCheck() {
        // A short warmup still needs a live sample: retaining zero would not
        // exercise the values users rely on when inspecting a paused flow.
        guard hasMeasuredThroughput else { return }
        pausedThroughput = displayedThroughput
        checks["throughput_before_pause"] = pausedThroughput
        pauseStarted = now
        model.setRunning(false)
        guard verifyRetainedThroughput("immediate_pause") else { return }
        stage = .pausing
        deadline = now + 20
    }

    private var displayedThroughput: [String: Double] {
        ["simulation_time_per_second": model.performance.simulationTimePerSecond,
         "initial_turnovers_per_second": model.performance.initialTurnoversPerSecond,
         "steps_per_second": model.performance.stepsPerSecond]
    }

    private var hasMeasuredThroughput: Bool {
        displayedThroughput.values.allSatisfy { $0.isFinite && $0 > 0 }
    }

    private func verifyRetainedThroughput(_ phase: String) -> Bool {
        let current = displayedThroughput
        let retained = current == pausedThroughput
        checks["throughput_at_\(phase)"] = current
        checks["throughput_retained_on_\(phase)"] = retained
        guard retained else {
            fail("Displayed throughput changed during \(phase).")
            return false
        }
        return true
    }

    private func verifyClearedThroughput(_ phase: String) -> Bool {
        let cleared = displayedThroughput.values.allSatisfy { $0 == 0 }
        checks["throughput_cleared_on_\(phase)"] = cleared
        guard cleared else {
            fail("A new run retained throughput from the previous flow during \(phase).")
            return false
        }
        return true
    }

    private func configurationReport() -> [String: Any] {
        let config = model.configuration
        return ["resolution": config.resolution, "padded_resolution": config.paddedResolution,
                "reynolds": config.reynolds, "viscosity": config.viscosity,
                "k0": config.initialWaveNumber, "seed": config.seed,
                "integrator": config.integrator.rawValue, "operator": config.nonlinearFormulation.rawValue,
                "cfl": config.cfl, "fixed_dt": config.fixedTimeStep as Any? ?? NSNull(),
                "batch": model.fixedBatchSteps as Any? ?? NSNull()]
    }

    private func presentationStatistics() -> [String: Any] {
        let times = model.presentationTimes
        let intervals = zip(times.dropFirst(), times).map { ($0 - $1) * 1000 }.sorted()
        let p95 = intervals.isEmpty ? 0 : intervals[min(intervals.count - 1, Int(Double(intervals.count) * 0.95))]
        let longest = intervals.last ?? 0
        return ["frames": model.totalPresentations,
                "p95_frame_interval_ms": p95, "longest_frame_interval_ms": longest,
                "display_target_hz": 60,
                "presented_frames_per_second": model.elapsedRunSeconds > 0 ? Double(model.totalPresentations) / model.elapsedRunSeconds : 0]
    }

    private func finishBenchmark() {
        guard stage == .measuring else { return }
        let reachedTarget = targetWallSeconds.map { model.elapsedRunSeconds >= $0 }
            ?? turboReachedEndpoint(model.time, target)
        // Wall runs stop at a completed GPU batch. Its actual simulation time is
        // the final frame target; no physical endpoint is imposed on the solver.
        let finalFrameTarget = physicalTarget ?? model.time
        guard model.elapsedRunSeconds > 0, reachedTarget,
              let finalFrame = model.frames.frame, model.frames.generation == model.generation,
              turboReachedEndpoint(finalFrame.time, finalFrameTarget),
              model.statistics?.isFinite == true, model.totalPresentations > 0 else {
            fail("GUI benchmark did not reach its endpoint with a finite, visible flow."); return
        }
        let seconds = model.elapsedRunSeconds
        let config = model.configuration
        let finite: Bool = model.statistics?.isFinite ?? false
        let report: [String: Any] = [
            "mode": "gui-benchmark", "passed": true, "device": model.deviceName,
            "seconds": seconds, "simulation_time_per_second": model.time / seconds,
            "initial_turnovers_per_second": model.initialTurnoverTime > 0 ? model.time / model.initialTurnoverTime / seconds : 0,
            "steps_per_second": Double(model.step) / seconds,
            "resolution": config.resolution, "padded_resolution": config.paddedResolution,
            "reynolds": config.reynolds, "k0": config.initialWaveNumber, "seed": config.seed,
            "integrator": config.integrator.rawValue, "cfl": config.cfl,
            "config": configurationReport(),
            "until": physicalTarget as Any? ?? NSNull(),
            "target_wall_seconds": targetWallSeconds as Any? ?? NSNull(),
            "wall_seconds_overshoot": targetWallSeconds.map { max(0, seconds - $0) } as Any? ?? NSNull(),
            "gui_warmup_seconds": warmupSeconds,
            "batch_policy": model.fixedBatchSteps == nil ? "adaptive-8ms-1to32" : "fixed",
            "batch_steps": model.fixedBatchSteps ?? model.performance.batchSteps,
            "display": model.display.rawValue, "palette": model.palette.rawValue,
            "exposure": model.exposure, "vorticity_sigma": model.vorticitySigma,
            "vorticity_color_range": model.vorticityColorRange.rawValue, "flow_overlay": false,
            "canvas": canvasReport(),
            "stats": ["simulation_time": model.time, "steps": model.step, "time_step": model.timeStep,
                      "energy": model.energy, "enstrophy": model.enstrophy,
                      "initial_energy": initialEnergy, "initial_enstrophy": initialEnstrophy,
                      "initial_turnover_time": model.initialTurnoverTime, "allocated_bytes": model.allocatedBytes,
                      "finite": finite],
            "presentation": presentationStatistics(), "controls": checks,
            "launch_to_window_seconds": launchSeconds
        ]
        finish(report, code: 0)
    }

    private func canvasReport() -> [String: Any] {
        guard let window = application?.windowController?.window, let content = window.contentView,
              let canvas = metalView(in: content) else { return [:] }
        return ["width_points": canvas.bounds.width, "height_points": canvas.bounds.height,
                "width_pixels": canvas.drawableSize.width, "height_pixels": canvas.drawableSize.height,
                "window_visible": window.isVisible && !window.isMiniaturized && !NSApp.isHidden,
                "window_size_points": [window.frame.width, window.frame.height]]
    }

    private func finishLiveCheck() {
        finish(["mode": "live-check", "passed": true, "controls": checks,
                "launch_to_window_seconds": launchSeconds,
                "resolution": model.configuration.resolution, "device": model.deviceName,
                "integrator": model.configuration.integrator.rawValue, "cfl": model.configuration.cfl,
                "config": configurationReport()], code: 0)
    }

    private func fail(_ message: String) {
        let finite: Bool = model.statistics?.isFinite ?? false
        let frameTime: Double = model.frames.frame?.time ?? -1
        finish(["mode": benchmark ? "gui-benchmark" : "live-check", "passed": false,
                "error": message, "phase": String(describing: stage),
                "controls": checks, "config": configurationReport(),
                "state": ["simulation_time": model.time, "target": physicalTarget as Any? ?? NSNull(),
                          "target_wall_seconds": targetWallSeconds as Any? ?? NSNull(),
                          "is_running": model.isRunning, "is_ready": model.isReady,
                          "is_settled": model.isSettled,
                          "elapsed_seconds": model.elapsedRunSeconds, "steps": model.step,
                          "frame_time": frameTime,
                          "generation": model.generation, "frame_generation": model.frames.generation,
                          "presentations": model.totalPresentations,
                          "finite": finite]], code: 1)
    }

    private func finish(_ report: [String: Any], code: Int32) {
        guard stage != .done else { return }
        let initialMetadata: [String: Any] = [
            "initial_file": model.loadedInitialFile as Any? ?? NSNull(),
            "initial_sha256": model.loadedInitialSHA256 as Any? ?? NSNull()
        ]
        var report = report.merging(initialMetadata) { _, captured in captured }
        // Read display configuration once, after timing; normal launches do not sample it.
        if let content = application?.windowController?.window?.contentView,
           let canvas = metalView(in: content), let layer = canvas.layer as? CAMetalLayer {
            report["rendering"] = [
                "rgba16_float": canvas.colorPixelFormat == .rgba16Float,
                "color_space": canvas.colorspace?.name as String? ?? "unknown",
                "wants_extended_dynamic_range": layer.wantsExtendedDynamicRangeContent,
                "current_headroom": canvas.window?.screen?.maximumExtendedDynamicRangeColorComponentValue ?? 1
            ]
        }
        stage = .done
        timer?.invalidate()
        timer = nil
        model.setRunning(false)
        if let exportImage, let frame = model.frames.frame, code == 0 {
            let palette = model.palette, display = model.display, exposure = model.exposure, flow = model.showFlowLines
            let vorticitySigma = model.vorticitySigma, vorticityColorRange = model.vorticityColorRange
            DispatchQueue.global(qos: .userInitiated).async { [weak self] in
                do {
                    let pixels = try TurboSnapshot.pngData(frame: frame, palette: palette, display: display,
                                                           exposure: exposure, vorticitySigma: vorticitySigma,
                                                           vorticityColorRange: vorticityColorRange,
                                                           showFlowLines: flow, pixelSize: 1200)
                    try FileManager.default.createDirectory(at: exportImage.deletingLastPathComponent(), withIntermediateDirectories: true)
                    try pixels.write(to: exportImage, options: .atomic)
                    var result = report
                    result["export_png"] = exportImage.path
                    DispatchQueue.main.async { self?.writeReport(result, code: code) }
                } catch {
                    let message = error.localizedDescription
                    DispatchQueue.main.async {
                        self?.writeReport(["passed": false, "error": "PNG export failed: \(message)"], code: 2)
                    }
                }
            }
        } else { writeReport(report, code: code) }
    }

    private func writeReport(_ report: [String: Any], code: Int32) {
        do {
            let data = try JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted, .sortedKeys])
            if let output {
                try FileManager.default.createDirectory(at: output.deletingLastPathComponent(), withIntermediateDirectories: true)
                try data.write(to: output, options: .atomic)
            }
            FileHandle.standardOutput.write(data)
            FileHandle.standardOutput.write(Data("\n".utf8))
            exit(code)
        } catch {
            FileHandle.standardError.write(Data("GUI diagnostic output failed: \(error.localizedDescription)\n".utf8))
            exit(2)
        }
    }
}
