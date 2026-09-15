import Combine
import Metal
import SwiftUI

/// Display choices never change the evolving spectral state.
enum TurboField: String, CaseIterable, Identifiable {
    case enstrophy, vorticity, speed
    var id: Self { self }
    var title: String {
        switch self {
        case .enstrophy: return "Enstrophy"
        case .vorticity: return "Vorticity"
        case .speed: return "Speed"
        }
    }
    var shaderIndex: UInt32 {
        switch self { case .vorticity: return 0; case .speed: return 1; case .enstrophy: return 2 }
    }
}

enum TurboColorRange: String, CaseIterable, Identifiable {
    case sigma, exposure
    var id: Self { self }
    var title: String { rawValue.capitalized }
    var shaderIndex: UInt32 { self == .sigma ? 0 : 1 }
}

enum TurboPalette: String, CaseIterable, Identifiable {
    case inferno, ember, aurora, glacier
    var id: Self { self }
    var title: String { rawValue.capitalized }
    var shaderIndex: UInt32 {
        switch self { case .aurora: return 0; case .ember: return 1; case .glacier: return 2; case .inferno: return 3 }
    }
    var colors: [Color] {
        switch self {
        case .inferno:
            return [Color(red: 0.06, green: 0.03, blue: 0.16), Color(red: 0.68, green: 0.16, blue: 0.28), Color(red: 0.99, green: 0.93, blue: 0.56)]
        case .ember:
            return [Color(red: 0.42, green: 0.14, blue: 0.57), Color(red: 0.12, green: 0.04, blue: 0.1), Color(red: 1, green: 0.64, blue: 0.27)]
        case .aurora:
            return [Color(red: 0.22, green: 0.25, blue: 0.82), Color(red: 0.06, green: 0.09, blue: 0.18), Color(red: 0.39, green: 0.94, blue: 0.73)]
        case .glacier:
            return [Color(red: 0.12, green: 0.29, blue: 0.58), Color(red: 0.03, green: 0.12, blue: 0.19), Color(red: 0.64, green: 0.94, blue: 1)]
        }
    }
}

/// Only the canvas observes frame deliveries; the inspector does not update 60 times/s.
@MainActor
final class DisplayFrameSource: ObservableObject {
    @Published var frame: FastTurboFrame?
    var generation: UInt64 = 0
}

/// Slider tracking and PID readouts only invalidate the small control views.
/// Publish one display snapshot, and skip samples whose visible values match.
@MainActor
final class LiveControlDisplay: ObservableObject {
    struct Values: Equatable {
        var cfl: Float
        var reynolds: Int
        var logarithmicReynolds: Double
        var isPalinstrophyRegulated: Bool
        var palinstrophy: String
    }

    @Published private(set) var values = Values(
        cfl: FastTurboConfiguration().cfl,
        reynolds: Int(FastTurboConfiguration().reynolds.rounded()),
        logarithmicReynolds: log10(FastTurboConfiguration().reynolds),
        isPalinstrophyRegulated: false, palinstrophy: "10⁴P/(Z kmax²) ≈ 0.00")

    func update(cfl: Float, reynolds: Double, regulated: Bool, palinstrophy: Double) {
        // Invalid programmatic configurations are reported by the solver; keep
        // the last readout rather than trapping during integer conversion.
        guard reynolds > 0, let integer = Int(exactly: reynolds.rounded()) else { return }
        let next = Values(cfl: cfl, reynolds: integer,
                          logarithmicReynolds: log10(reynolds),
                          isPalinstrophyRegulated: regulated,
                          palinstrophy: String(format: "10⁴P/(Z kmax²) ≈ %.2f", palinstrophy))
        if next != values { values = next }
    }
}

struct TurboPerformance {
    var simulationTimePerSecond = 0.0
    var initialTurnoversPerSecond = 0.0
    var stepsPerSecond = 0.0
    var millisecondsPerStep = 0.0
    var batchSteps = 1
}

/// Retains completed automatic advance and active wall time for the whole run.
/// The clock spans presentation/scheduling gaps, but excludes preparation before
/// the first batch of each running segment, paused idle time, and manual steps.
struct RunThroughputAverage {
    private(set) var simulationTime = 0.0
    private var accumulatedWall = 0.0
    private var runningSince: Double?

    mutating func beginBatch(automatic: Bool, at time: Double) {
        if automatic {
            if runningSince == nil { runningSince = time }
        } else {
            stop(at: time)
        }
    }

    mutating func completeBatch(from before: Double, to after: Double, automatic: Bool) {
        guard automatic, before.isFinite, after.isFinite, after >= before else { return }
        simulationTime += after - before
    }

    mutating func stop(at time: Double) {
        if let start = runningSince { accumulatedWall += max(0, time - start) }
        runningSince = nil
    }

    func wallTime(at time: Double) -> Double {
        accumulatedWall + (runningSince.map { max(0, time - $0) } ?? 0)
    }

    func rate(at time: Double) -> Double {
        let elapsed = wallTime(at: time)
        return elapsed > 0 ? simulationTime / elapsed : 0
    }
}

extension Integrator {
    var displayTitle: String {
        switch self {
        case .imexRK3: return "IMEX RK3"
        case .ifHeunRK3: return "Integrating-factor RK3"
        case .etdrk4: return "ETD RK4"
        }
    }
}

/// Match the CLI's relative endpoint tolerance for the compensated GPU clock.
func turboReachedEndpoint(_ time: Double, _ target: Double) -> Bool {
    let tolerance = max(abs(target), Double.leastNormalMagnitude) * (32 * Double.ulpOfOne)
    return time >= target - tolerance
}
