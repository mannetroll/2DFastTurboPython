// Thin C ABI. The reference numerical engine and native pump are compiled unchanged.
import AppKit
import Foundation
import Metal
import MetalKit
import SwiftUI

final class FastTurboResources {
    static var directory = ""
    static var computeDevice: MTLDevice?
    static func library(device: MTLDevice) -> MTLLibrary? {
        try? device.makeLibrary(URL: URL(fileURLWithPath: directory).appendingPathComponent("default.metallib"))
    }
}

private struct PortError: LocalizedError {
    let message: String
    var errorDescription: String? { message }
}

private func decode(_ input: UnsafePointer<CChar>?) throws -> [String: Any] {
    guard let input, let data = String(cString: input).data(using: .utf8),
          let object = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
        throw PortError(message: "Expected a JSON object.")
    }
    return object
}

private func response(_ body: () throws -> [String: Any]) -> UnsafeMutablePointer<CChar>? {
    let result: [String: Any]
    do { result = try autoreleasepool(invoking: body) }
    catch { result = ["error": error.localizedDescription] }
    do {
        let data = try JSONSerialization.data(withJSONObject: result, options: [.sortedKeys])
        return strdup(String(decoding: data, as: UTF8.self))
    } catch { return strdup("{\"error\":\"Could not serialize native response.\"}") }
}

private func configuration(_ values: [String: Any], base: FastTurboConfiguration = FastTurboConfiguration()) throws -> FastTurboConfiguration {
    var c = base
    if let value = values["resolution"] as? Int { c.resolution = value }
    if let value = values["reynolds"] as? Double { c.reynolds = value }
    if let value = values["k0"] as? Int { c.initialWaveNumber = value }
    if let value = values["seed"] {
        guard let seed = UInt64(String(describing: value)) else { throw PortError(message: "Seed must be UInt64.") }
        c.seed = seed
    }
    if let value = values["cfl"] as? Double { c.cfl = Float(value) }
    if let value = values["integrator"] as? String {
        guard let method = Integrator(rawValue: value) else { throw PortError(message: "Unknown integrator.") }
        c.integrator = method
        if method == .cnab2, base.integrator != method, values["cfl"] == nil { c.cfl = 0.25 }
    }
    if let value = values["operator"] as? String {
        guard let form = NonlinearFormulation(rawValue: value) else { throw PortError(message: "Unknown nonlinear form.") }
        c.nonlinearFormulation = form
    }
    if let value = values["start_spectrum"] as? String {
        guard let spectrum = StartSpectrum(rawValue: value) else { throw PortError(message: "Unknown start spectrum.") }
        c.startSpectrum = spectrum
    }
    if let value = values["fixed_dt"] { c.fixedTimeStep = (value as? Double).map(Float.init) }
    guard (16...16384).contains(c.resolution), c.resolution % 2 == 0,
          c.initialWaveNumber > 0, c.initialWaveNumber <= 1_000_000,
          c.reynolds.isFinite, c.reynolds > 0, c.viscosity.isFinite,
          c.cfl.isFinite, c.cfl > 0,
          c.fixedTimeStep.map({ $0.isFinite && $0 > 0 }) ?? true else {
        throw PortError(message: "Invalid grid, Reynolds number, wave number, CFL or fixed timestep.")
    }
    try c.validateCNAB2()
    return c
}

private func configReport(_ c: FastTurboConfiguration) -> [String: Any] {
    ["resolution": c.resolution, "padded_resolution": c.paddedResolution,
     "reynolds": c.reynolds, "viscosity": c.viscosity, "k0": c.initialWaveNumber,
     "seed": String(c.seed), "cfl": c.cfl, "integrator": c.integrator.rawValue,
     "operator": c.nonlinearFormulation.rawValue, "start_spectrum": c.startSpectrum.rawValue,
     "fixed_dt": c.fixedTimeStep as Any? ?? NSNull()]
}

private func statsReport(_ s: FastTurboStatistics) -> [String: Any] {
    ["steps": s.step, "simulation_time": s.time, "dt": s.timeStep, "energy": s.energy,
     "enstrophy": s.enstrophy, "palinstrophy": s.palinstrophy,
     "palinstrophy_ratio": s.palinstrophyOverEnstrophyKmaxSquared,
     "reynolds": s.reynolds, "initial_turnover_time": s.initialTurnoverTime,
     "gpu_milliseconds": s.gpuMilliseconds, "allocated_bytes": s.allocatedBytes, "is_finite": s.isFinite]
}

private func readField(_ path: String?, n: Int) throws -> [Float]? {
    guard let path else { return nil }
    let data = try Data(contentsOf: URL(fileURLWithPath: path))
    guard data.count == n * n * 4 else { throw PortError(message: "Initial field must contain exactly N² Float32 samples.") }
    return data.withUnsafeBytes { bytes in
        (0..<(n * n)).map { Float(bitPattern: bytes.loadUnaligned(fromByteOffset: $0 * 4, as: UInt32.self).littleEndian) }
    }
}

private func write(_ data: Data, to path: String) throws {
    try data.write(to: URL(fileURLWithPath: path), options: .atomic)
}

private func frameReport(_ f: FastTurboFrame) -> [String: Any] {
    ["sequence": f.sequence, "time": f.time, "grid_size": f.gridSize,
     "max_vorticity": f.maxVorticity, "mean_vorticity": f.meanVorticity,
     "std_vorticity": f.standardDeviationVorticity, "max_speed": f.maxSpeed]
}

private func exportFrame(_ frame: FastTurboFrame, request: [String: Any]) throws {
    if let path = request["fields"] as? String {
        try write(Data(bytes: frame.buffer.contents(), count: 3 * frame.gridSize * frame.gridSize * 4), to: path)
    }
    if let path = request["png"] as? String {
        let palette = TurboPalette(rawValue: request["palette"] as? String ?? "ember")
        let field = TurboField(rawValue: request["display"] as? String ?? "vorticity")
        let range = TurboColorRange(rawValue: request["color_range"] as? String ?? "sigma")
        guard let palette, let field, let range else { throw PortError(message: "Unknown rendering option.") }
        let data = try TurboSnapshot.pngData(frame: frame, palette: palette, display: field,
                    exposure: request["exposure"] as? Double ?? 1.2,
                    vorticitySigma: request["sigma"] as? Double ?? 2.5, vorticityColorRange: range,
                    showFlowLines: request["flow"] as? Bool ?? false, pixelSize: request["pixel_size"] as? Int ?? 1800)
        try write(data, to: path)
    }
}

@_cdecl("ft_string_free")
public func ftStringFree(_ pointer: UnsafeMutablePointer<CChar>?) { free(pointer) }

@_cdecl("ft_initialize")
public func ftInitialize(_ directory: UnsafePointer<CChar>?) -> UnsafeMutablePointer<CChar>? {
    response {
        guard Thread.isMainThread else { throw PortError(message: "Initialize FastTurbo on the main thread.") }
        if let directory { FastTurboResources.directory = String(cString: directory) }
        guard MTTVkFFTWasCompiledIn() else { throw PortError(message: "VkFFT was not compiled in.") }
        let device: MTLDevice
        var deviceIndex = 0
        if let value = ProcessInfo.processInfo.environment["FASTTURBO_DEVICE"] {
            let devices = MTLCopyAllDevices()
            guard let index = Int(value), devices.indices.contains(index) else {
                throw PortError(message: "Invalid Metal device index: \(value)")
            }
            deviceIndex = index
            device = devices[index]
        } else {
            guard let selected = MTLCreateSystemDefaultDevice() else { throw FastTurboError.noMetalDevice }
            device = selected
        }
        FastTurboResources.computeDevice = device
        return ["abi_version": 1, "defaults": configReport(FastTurboConfiguration()),
                "backend": "Metal / VkFFT", "precision": "float32", "device_index": deviceIndex,
                "integrators": Integrator.allCases.map(\.rawValue),
                "unified_memory": device.hasUnifiedMemory,
                "gpu": device.name, "os": ProcessInfo.processInfo.operatingSystemVersionString,
                "recommended_working_set_bytes": device.recommendedMaxWorkingSetSize,
                "physical_memory_bytes": ProcessInfo.processInfo.physicalMemory]
    }
}

private final class EngineHandle {
    let core: FastTurboCore
    init(_ request: [String: Any]) throws {
        let c = try configuration(request)
        core = try FastTurboCore(configuration: c, device: FastTurboResources.computeDevice,
                                 vorticity: readField(request["initial"] as? String, n: c.resolution))
    }
}

@_cdecl("ft_engine_create")
public func ftEngineCreate(_ json: UnsafePointer<CChar>?, _ errorOut: UnsafeMutablePointer<UnsafeMutablePointer<CChar>?>?) -> UnsafeMutableRawPointer? {
    do { return try autoreleasepool { Unmanaged.passRetained(try EngineHandle(decode(json))).toOpaque() } }
    catch { errorOut?.pointee = strdup(error.localizedDescription); return nil }
}

@_cdecl("ft_engine_destroy")
public func ftEngineDestroy(_ pointer: UnsafeMutableRawPointer?) {
    if let pointer { Unmanaged<EngineHandle>.fromOpaque(pointer).release() }
}

@_cdecl("ft_engine_call")
public func ftEngineCall(_ pointer: UnsafeMutableRawPointer?, _ json: UnsafePointer<CChar>?) -> UnsafeMutablePointer<CChar>? {
    response {
        guard let pointer else { throw PortError(message: "Closed engine.") }
        let core = Unmanaged<EngineHandle>.fromOpaque(pointer).takeUnretainedValue().core
        let request = try decode(json)
        switch request["op"] as? String {
        case "advance":
            let steps = request["steps"] as? Int ?? 1
            return statsReport(try core.advanceSync(steps: steps, until: request["until"] as? Double))
        case "inspect": return statsReport(try core.inspectSync())
        case "set":
            if let value = request["reynolds"] as? Double { try core.setReynoldsSync(value) }
            if let value = request["cfl"] as? Double { try core.setCFLSync(Float(value)) }
            return [:]
        case "reset":
            try core.resetSync(vorticity: readField(request["initial"] as? String, n: core.configuration.resolution))
            return statsReport(try core.inspectSync())
        case "export":
            if let path = request["spectrum"] as? String {
                let values = try core.spectrumSync()
                try write(values.withUnsafeBytes { Data($0) }, to: path)
            }
            var result: [String: Any] = [:]
            if request["snapshot"] as? Bool == true || request["fields"] != nil || request["png"] != nil {
                guard let frame = try core.snapshotSync(includeVelocity: request["include_velocity"] as? Bool ?? true) else {
                    throw PortError(message: "No snapshot slot available.")
                }
                try exportFrame(frame, request: request)
                result = frameReport(frame)
            }
            return result
        default: throw PortError(message: "Unknown engine operation.")
        }
    }
}

// Only the native canvas observes 60Hz frame publication. Python never handles frames.
private struct PortCanvas: View {
    @ObservedObject var model: SimulationController
    var body: some View {
        ZStack {
            FastTurboCanvas(source: model.frames, palette: model.palette, display: model.display,
                exposure: model.exposure, vorticitySigma: model.vorticitySigma,
                vorticityColorRange: model.vorticityColorRange, showFlowLines: model.showFlowLines,
                onError: { model.statusMessage = $0 }, onPresented: model.recordPresentation)
            if !model.isReady {
                VStack(spacing: 13) {
                    if model.statusMessage == nil { ProgressView().controlSize(.small) }
                    Text(model.statusMessage == nil ? "Preparing the flow" : "Unable to start the flow")
                        .font(.system(size: 12))
                }.frame(maxWidth: .infinity, maxHeight: .infinity).background(Color(red: 0.035, green: 0.047, blue: 0.073))
            }
            VStack {
                HStack {
                    Text("UNFORCED · SEED \(model.configuration.seed)")
                        .font(.system(size: 8, weight: .medium, design: .monospaced)).tracking(1)
                        .padding(.horizontal, 10).padding(.vertical, 7).background(.black.opacity(0.3), in: Capsule())
                    Spacer()
                }
                Spacer()
                HStack {
                    HStack(spacing: 0) {
                        Button { model.togglePause() } label: { Image(systemName: model.isRunning ? "pause.fill" : "play.fill").frame(width: 38, height: 35) }.help("Pause / resume · Space")
                        Button { model.singleStep() } label: { Image(systemName: "forward.frame.fill").frame(width: 38, height: 35) }.help("Single step · →")
                        Button { model.reset() } label: { Image(systemName: "arrow.counterclockwise").frame(width: 38, height: 35) }.help("Reset · R")
                    }.buttonStyle(.plain).disabled(!model.isReady).background(.ultraThinMaterial, in: Capsule())
                    Spacer()
                    Text(String(format: "t  %.3f", model.time)).font(.system(size: 10, design: .monospaced))
                        .padding(.horizontal, 11).padding(.vertical, 8).background(.black.opacity(0.3), in: Capsule())
                }
            }.padding(14)
        }.foregroundStyle(.white.opacity(0.9)).preferredColorScheme(.dark)
    }
}

@MainActor
private final class UIHandle {
    let model = SimulationController()
    var view: NSView?
    var observers: [NSObjectProtocol] = []
    func report() -> [String: Any] {
        var result: [String: Any] = ["config": configReport(model.configuration),
            "ready": model.isReady, "running": model.isRunning, "settled": model.isSettled,
            "generation": model.generation, "gpu": model.deviceName,
            "error_message": model.statusMessage as Any? ?? NSNull(),
            "regulated": model.isPalinstrophyRegulated, "effective_reynolds": model.effectiveReynolds,
            "palinstrophy_control": model.palinstrophyControlValue,
            "display": model.display.rawValue, "palette": model.palette.rawValue,
            "exposure": model.exposure, "sigma": model.vorticitySigma,
            "color_range": model.vorticityColorRange.rawValue, "flow": model.showFlowLines,
            "focused": model.isFocusMode, "exporting": model.isExporting,
            "elapsed_seconds": model.elapsedRunSeconds, "presentations": model.totalPresentations,
            "allocated_bytes": model.allocatedBytes, "history": model.history,
            "performance": ["simtime_per_s": model.performance.simulationTimePerSecond,
                "turnovers_per_s": model.performance.initialTurnoversPerSecond,
                "steps_per_s": model.performance.stepsPerSecond,
                "milliseconds_per_step": model.performance.millisecondsPerStep,
                "batch_steps": model.performance.batchSteps]]
        if let s = model.statistics { result["stats"] = statsReport(s) }
        if let f = model.frames.frame { result["frame"] = frameReport(f) }
        if let view {
            result["canvas"] = ["width_points": view.bounds.width, "height_points": view.bounds.height,
                "visible": view.window?.isVisible == true && view.window?.isMiniaturized == false && !NSApp.isHidden,
                "scale": view.window?.backingScaleFactor ?? 1]
        }
        return result
    }
    func attach(_ parent: NSView) {
        view?.removeFromSuperview()
        let host = NSHostingView(rootView: PortCanvas(model: model))
        host.frame = parent.bounds
        host.autoresizingMask = [.width, .height]
        parent.addSubview(host)
        view = host
        let center = NotificationCenter.default
        for name in [NSApplication.didHideNotification, NSApplication.didUnhideNotification,
                     NSWindow.didChangeOcclusionStateNotification, NSWindow.didMiniaturizeNotification,
                     NSWindow.didDeminiaturizeNotification] {
            observers.append(center.addObserver(forName: name, object: nil, queue: .main) { [weak self] _ in
                MainActor.assumeIsolated {
                    guard let self, let window = self.view?.window else { return }
                    self.model.setVisible(window.isVisible && !window.isMiniaturized && !NSApp.isHidden && window.occlusionState.contains(.visible))
                }
            })
        }
    }
    func close() {
        model.setRunning(false)
        model.setVisible(false)
        for observer in observers { NotificationCenter.default.removeObserver(observer) }
        observers.removeAll()
        view?.removeFromSuperview()
        view = nil
    }
}

@_cdecl("ft_ui_create")
public func ftUICreate() -> UnsafeMutableRawPointer? {
    guard Thread.isMainThread else { return nil }
    let handle = MainActor.assumeIsolated { _ = NSApplication.shared; return UIHandle() }
    return Unmanaged.passRetained(handle).toOpaque()
}

@_cdecl("ft_ui_destroy")
public func ftUIDestroy(_ pointer: UnsafeMutableRawPointer?) {
    guard Thread.isMainThread, let pointer else { return }
    MainActor.assumeIsolated {
        let handle = Unmanaged<UIHandle>.fromOpaque(pointer)
        handle.takeUnretainedValue().close()
        handle.release()
    }
}

@_cdecl("ft_ui_attach")
public func ftUIAttach(_ pointer: UnsafeMutableRawPointer?, _ parent: UnsafeMutableRawPointer?) -> UnsafeMutablePointer<CChar>? {
    response {
        guard Thread.isMainThread, let pointer, let parent else { throw PortError(message: "Attach requires the main thread and a parent NSView.") }
        return MainActor.assumeIsolated {
            let handle = Unmanaged<UIHandle>.fromOpaque(pointer).takeUnretainedValue()
            handle.attach(Unmanaged<NSView>.fromOpaque(parent).takeUnretainedValue())
            return [:]
        }
    }
}

@_cdecl("ft_ui_call")
public func ftUICall(_ pointer: UnsafeMutableRawPointer?, _ json: UnsafePointer<CChar>?) -> UnsafeMutablePointer<CChar>? {
    response {
        guard Thread.isMainThread, let pointer else { throw PortError(message: "UI calls require a live handle on the main thread.") }
        let request = try decode(json)
        return try MainActor.assumeIsolated {
            let handle = Unmanaged<UIHandle>.fromOpaque(pointer).takeUnretainedValue()
            let model = handle.model
            switch request["op"] as? String {
            case "get": break
            case "start": model.start()
            case "toggle": model.togglePause()
            case "running": model.setRunning(request["value"] as? Bool ?? false)
            case "step": model.singleStep()
            case "reset": model.reset()
            case "seed": model.newSeed()
            case "visible": model.setVisible(request["value"] as? Bool ?? true)
            case "flush": model.flushLiveParameters()
            case "save": model.saveSnapshot()
            case "dismiss": model.statusMessage = nil
            case "configure":
                var values = request
                if request["cap_cfl"] as? Bool == true, request["integrator"] as? String == "CNAB2" { values["operator"] = "stress" }
                var next = try configuration(values, base: model.configuration)
                if request["cap_cfl"] as? Bool == true, next.integrator != model.configuration.integrator {
                    if next.integrator == .cnab2 { next.cfl = min(next.cfl, 0.25) }
                    else {
                        next.cfl = max(next.cfl, 1)
                        if next.integrator != .etdrk4 { next.cfl = min(next.cfl, 1.5) }
                    }
                }
                model.configuration = next
            case "set":
                if let value = request["regulated"] as? Bool { model.isPalinstrophyRegulated = value }
                if let value = request["cfl"] as? Double { model.setCFL(value) }
                if let value = request["log_reynolds"] as? Double { model.setLogarithmicReynolds(value) }
                if let name = request["display"] as? String {
                    guard let value = TurboField(rawValue: name) else { throw PortError(message: "Unknown field.") }
                    if model.display != value { model.display = value }
                }
                if let name = request["palette"] as? String {
                    guard let value = TurboPalette(rawValue: name) else { throw PortError(message: "Unknown palette.") }
                    model.palette = value
                }
                if let name = request["color_range"] as? String {
                    guard let value = TurboColorRange(rawValue: name) else { throw PortError(message: "Unknown color range.") }
                    model.vorticityColorRange = value
                }
                if let value = request["sigma"] as? Double, value.isFinite { model.vorticitySigma = min(3.5, max(1.5, value)) }
                if let value = request["exposure"] as? Double, value.isFinite { model.exposure = min(3, max(0.25, value)) }
                if let value = request["flow"] as? Bool, value != model.showFlowLines { model.showFlowLines = value }
                if let value = request["focused"] as? Bool { model.isFocusMode = value }
            case "benchmark":
                model.targetTime = request["until"] as? Double
                model.targetWallSeconds = request["wall_seconds"] as? Double
                if let value = request["batch"] as? Int, !(1...32).contains(value) { throw PortError(message: "Batch must be 1…32.") }
                model.fixedBatchSteps = request["batch"] as? Int
                model.initialFieldURL = (request["initial"] as? String).map { URL(fileURLWithPath: $0) }
                model.spectrumOutputURL = (request["spectrum"] as? String).map { URL(fileURLWithPath: $0) }
            case "export":
                guard let frame = model.frames.frame else { throw PortError(message: "No completed frame.") }
                var options = request
                options["display"] = model.display.rawValue; options["palette"] = model.palette.rawValue
                options["exposure"] = model.exposure; options["sigma"] = model.vorticitySigma
                options["color_range"] = model.vorticityColorRange.rawValue; options["flow"] = model.showFlowLines
                try exportFrame(frame, request: options)
            default: throw PortError(message: "Unknown UI operation.")
            }
            return handle.report()
        }
    }
}
