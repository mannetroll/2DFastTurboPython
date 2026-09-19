import Foundation
import Metal

public enum Integrator: String, CaseIterable, Codable, Sendable, Identifiable {
    case imexRK3, ifHeunRK3, etdrk4
    case cnab2 = "CNAB2"
    public var id: Self { self }
    public var title: String {
        switch self {
        case .imexRK3: return "IMEX RK3"
        case .ifHeunRK3: return "IF Heun RK3"
        case .etdrk4: return "ETD RK4"
        case .cnab2: return "CNAB2"
        }
    }
    /// Transform count with the stress nonlinear formulation.
    public var transformsPerStep: Int { self == .cnab2 ? 5 : (self == .etdrk4 ? 16 : 12) }
    var shaderIndex: UInt32 {
        switch self { case .imexRK3: return 0; case .ifHeunRK3: return 1; case .etdrk4: return 2; case .cnab2: return 3 }
    }
}

public enum NonlinearFormulation: String, CaseIterable, Codable, Sendable {
    case stress, advective
    var shaderIndex: UInt32 { self == .stress ? 0 : 1 }
}

public enum StartSpectrum: String, CaseIterable, Codable, Sendable {
    case pao = "PAO", km3 = "KM3"
}

public struct FastTurboConfiguration: Equatable, Sendable {
    public var resolution = 1024
    public var reynolds: Double
    public var initialWaveNumber = 4
    public var seed: UInt64 = 2
    public var cfl: Float = 3
    public var integrator: Integrator = .etdrk4
    public var nonlinearFormulation: NonlinearFormulation = .stress
    public var startSpectrum: StartSpectrum = .pao
    /// Overrides the CFL controller for convergence tests. Endpoint clipping still applies.
    public var fixedTimeStep: Float? = nil
    public var paddedResolution: Int { 3 * resolution / 2 }
    public var viscosity: Float { integrator == .cnab2 ? 1 / Float(reynolds) : Float(1 / reynolds) }
    public var transformsPerStep: Int {
        integrator == .cnab2 ? 5 : (integrator == .etdrk4 ? 4 : 3) * (nonlinearFormulation == .stress ? 4 : 5)
    }

    func validateCNAB2() throws {
        if integrator == .cnab2 && (resolution < 16 || resolution > 16384 || resolution & (resolution - 1) != 0
            || !(1...5010).contains(seed) || nonlinearFormulation != .stress
            || !Float(reynolds).isFinite || !(viscosity > 0) || !viscosity.isFinite) {
            throw FastTurboError.invalidConfiguration("CNAB2 requires a power-of-two resolution, seed 1..5010, stress form and a representable positive viscosity.")
        }
    }
    /// Empirical initial Reynolds number used by the palinstrophy controller.
    /// This is the Swift equivalent of `Re_from_N_K0(N, K0)` in palinstrophy.
    public static func reynoldsFromNAndK0(_ resolution: Int, _ initialWaveNumber: Int) -> Double {
        let log10Re = 1.066_240 * log10(Double(resolution))
            - 0.083_911 * log10(Double(initialWaveNumber)) + 1.286_396
        return pow(10, log10Re)
    }

    public init() {
        reynolds = Self.reynoldsFromNAndK0(resolution, initialWaveNumber)
    }
}

public struct FastTurboStatistics: Sendable {
    public var step: UInt64 = 0
    public var time: Double = 0
    public var timeStep: Float = 0
    /// Refreshed by inspect(), including the initial inspection, rather than every advance batch.
    public var energy: Double = 0
    public var enstrophy: Double = 0
    /// Spectral palinstrophy, evaluated on the solver's retained Fourier band.
    public var palinstrophy: Double = 0
    /// `palinstrophy / (enstrophy * kmax²)`, used by the optional Reynolds PID.
    public var palinstrophyOverEnstrophyKmaxSquared: Double = 0
    /// The effective Reynolds number used for the next solver batch.
    public var reynolds: Double = 0
    public var initialTurnoverTime: Double = 0
    /// GPU duration of the most recent advance batch, excluding snapshots and diagnostics.
    public var gpuMilliseconds: Double = 0
    /// Explicitly owned Metal buffers; VkFFT's internal plan allocations are additional.
    public var allocatedBytes: Int = 0
    public var isFinite: Bool = true
    public init() {}
}

/// An immutable snapshot lease. Retain the frame until the renderer's command buffer retires.
/// The buffer contains three contiguous row-major Float32 planes: physical omega, u, v.
/// Vorticity uses omega = d(v)/dx - d(u)/dy on the periodic [0, 2*pi)² domain.
public final class FastTurboFrame: @unchecked Sendable {
    public let buffer: MTLBuffer
    public let gridSize: Int
    public let sequence: UInt64
    public let time: Double
    public let maxVorticity: Float
    public let meanVorticity: Float
    public let standardDeviationVorticity: Float
    public let maxSpeed: Float
    private let release: @Sendable () -> Void

    init(buffer: MTLBuffer, gridSize: Int, sequence: UInt64, time: Double,
         maxVorticity: Float, meanVorticity: Float, standardDeviationVorticity: Float,
         maxSpeed: Float, release: @escaping @Sendable () -> Void) {
        self.buffer = buffer
        self.gridSize = gridSize
        self.sequence = sequence
        self.time = time
        self.maxVorticity = maxVorticity
        self.meanVorticity = meanVorticity
        self.standardDeviationVorticity = standardDeviationVorticity
        self.maxSpeed = maxSpeed
        self.release = release
    }

    deinit { release() }
}

public enum FastTurboError: LocalizedError {
    case noMetalDevice
    case invalidConfiguration(String)
    case metal(String)
    case nonFiniteState
    public var errorDescription: String? {
        switch self {
        case .noMetalDevice: return "A Metal device is unavailable in this process."
        case .invalidConfiguration(let detail): return detail
        case .metal(let detail): return detail
        case .nonFiniteState: return "The solver encountered a non-finite field or timestep."
        }
    }
}
