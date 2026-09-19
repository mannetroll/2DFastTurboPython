import Foundation
import Metal
import XCTest

final class FastTurboAPITests: XCTestCase {
    private func device() throws -> MTLDevice {
        guard let device = MTLCreateSystemDefaultDevice() else {
            throw XCTSkip("Metal device access is required for GPU API tests.")
        }
        return device
    }

    func testAsyncCompletionCanCallSynchronousAPIs() throws {
        var configuration = FastTurboConfiguration()
        configuration.resolution = 32
        configuration.fixedTimeStep = 0.002
        let core = try FastTurboCore(configuration: configuration, device: device())
        let completed = expectation(description: "Synchronous calls return inside completion")
        core.advance(steps: 7) { result in
            defer { completed.fulfill() }
            do {
                let advanced = try result.get()
                XCTAssertFalse(Thread.isMainThread)
                XCTAssertEqual(try core.inspectSync().step, advanced.step)
                XCTAssertEqual(try core.spectrumSync().count, 32 * 32)
                let frame = try XCTUnwrap(core.snapshotSync())
                XCTAssertEqual(frame.time, advanced.time)
                try core.resetSync()
                XCTAssertEqual(try core.inspectSync().step, 0)
                XCTAssertEqual(try core.advanceSync(steps: 1).step, 1)
            } catch {
                XCTFail("Reentrant synchronous API failed: \(error)")
            }
        }
        wait(for: [completed], timeout: 10)
        XCTAssertEqual(try core.inspectSync().step, 1)
    }

    func testSnapshotVorticityStatisticsMatchCurrentPhysicalField() throws {
        var configuration = FastTurboConfiguration()
        configuration.resolution = 256
        configuration.fixedTimeStep = 0.002
        let core = try FastTurboCore(configuration: configuration, device: device())

        func checkSnapshot(includeVelocity: Bool) throws {
            let frame = try XCTUnwrap(core.snapshotSync(includeVelocity: includeVelocity))
            let count = frame.gridSize * frame.gridSize
            let values = frame.buffer.contents().bindMemory(to: Float.self, capacity: 3 * count)
            let omega = (0..<count).map { Double(values[$0]) }
            let mean = omega.reduce(0, +) / Double(count)
            let variance = omega.reduce(0) { $0 + ($1 - mean) * ($1 - mean) } / Double(count)
            let sigma = sqrt(variance)
            XCTAssertEqual(Double(frame.meanVorticity), mean, accuracy: max(sigma * 1e-6, 1e-12))
            XCTAssertEqual(Double(frame.standardDeviationVorticity), sigma, accuracy: max(sigma * 1e-6, 1e-12))
            XCTAssertEqual(Double(frame.maxVorticity), omega.map { abs($0) }.max()!)
            let maxSpeed = (0..<count).map { i in
                hypot(Double(values[count + i]), Double(values[2 * count + i]))
            }.max()!
            XCTAssertEqual(Double(frame.maxSpeed), maxSpeed, accuracy: max(maxSpeed * 1e-6, 1e-12))
        }

        try checkSnapshot(includeVelocity: false)
        _ = try core.advanceSync(steps: 8)
        try checkSnapshot(includeVelocity: true)
        try core.resetSync(vorticity: Array(repeating: 0, count: 256 * 256))
        try checkSnapshot(includeVelocity: false)
    }

    func testNonFiniteAdvanceRemainsFailedUntilSuccessfulReset() throws {
        var configuration = FastTurboConfiguration()
        configuration.resolution = 32
        configuration.reynolds = .infinity
        configuration.fixedTimeStep = Float.greatestFiniteMagnitude
        configuration.nonlinearFormulation = .advective
        let n = configuration.resolution
        // Different wave-number magnitudes create nonzero advection. The finite
        // but excessive fixed timestep reliably overflows that physical update.
        let field = (0..<(n * n)).map { i in
            Float(10 * cos(2 * Double.pi * Double(i % n) / Double(n))
                  + 10 * cos(4 * Double.pi * Double(i / n) / Double(n)))
        }
        let core = try FastTurboCore(configuration: configuration, device: device(), vorticity: field)
        let initial = try core.spectrumSync()
        XCTAssertThrowsError(try core.advanceSync(steps: 1)) { error in
            guard case FastTurboError.nonFiniteState = error else {
                XCTFail("Expected the finite input to overflow during advance, received \(error)")
                return
            }
        }
        XCTAssertThrowsError(try core.advanceSync(steps: 0))
        XCTAssertThrowsError(try core.inspectSync())
        XCTAssertThrowsError(try core.snapshotSync())
        XCTAssertThrowsError(try core.spectrumSync())

        // An invalid recovery request must not clear the previous terminal error.
        XCTAssertThrowsError(try core.resetSync(vorticity: [0]))
        XCTAssertThrowsError(try core.spectrumSync())
        try core.resetSync(vorticity: field)
        let reset = try core.inspectSync()
        XCTAssertTrue(reset.isFinite)
        XCTAssertEqual(reset.step, 0)
        XCTAssertEqual(reset.time, 0)
        XCTAssertEqual(try core.spectrumSync(), initial)
        XCTAssertNotNil(try core.snapshotSync())
    }

    func testReynoldsCanChangeBetweenCompletedBatchesAndReportsPalinstrophy() throws {
        var configuration = FastTurboConfiguration()
        configuration.resolution = 32
        configuration.reynolds = 20_000
        configuration.fixedTimeStep = 0.002
        let core = try FastTurboCore(configuration: configuration, device: device())
        let initial = try core.inspectSync()
        XCTAssertGreaterThan(initial.palinstrophy, 0)
        XCTAssertGreaterThan(initial.palinstrophyOverEnstrophyKmaxSquared, 0)
        XCTAssertEqual(initial.reynolds, 20_000)

        try core.setReynoldsSync(80_000)
        XCTAssertEqual(try core.advanceSync(steps: 1).reynolds, 80_000)
        XCTAssertEqual(try core.inspectSync().reynolds, 80_000)

        // The non-blocking path is used for slider drags. Queue order makes the
        // value effective before the next submitted advance batch.
        core.setReynolds(40_000)
        XCTAssertEqual(try core.advanceSync(steps: 1).reynolds, 40_000)
        XCTAssertThrowsError(try core.setReynoldsSync(0))
    }

    func testInitialReynoldsUsesPalinstrophyCalibration() {
        XCTAssertEqual(FastTurboConfiguration().reynolds, 27_898.36614974335, accuracy: 1e-8)
        XCTAssertEqual(FastTurboConfiguration.reynoldsFromNAndK0(1024, 4),
                       27_898.36614974335, accuracy: 1e-8)
    }

    func testLiveCFLAndReynoldsKeepStateAndAffectNextAdaptiveStep() throws {
        let n = 32
        // A single Fourier mode has zero nonlinear advection, so its decay
        // independently verifies that the new viscosity reaches each integrator.
        let field: [Float] = (0..<(n * n)).map { i in
            let phase = 2 * Double.pi * Double(6 * (i % n) + 8 * (i / n)) / Double(n)
            return Float(20 * cos(phase))
        }
        for integrator in Integrator.allCases {
            for formulation in NonlinearFormulation.allCases where integrator != .cnab2 || formulation == .stress {
                var configuration = FastTurboConfiguration()
                configuration.resolution = n
                configuration.reynolds = 10_000
                configuration.cfl = integrator == .cnab2 ? 0.25 : 3
                configuration.integrator = integrator
                configuration.nonlinearFormulation = formulation
                let core = try FastTurboCore(configuration: configuration, device: device(), vorticity: field)
                _ = try core.advanceSync(steps: 4)
                let before = try core.inspectSync()
                let spectrum = try core.spectrumSync()
                let frame = try XCTUnwrap(core.snapshotSync())
                let frameBytes = Data(bytes: frame.buffer.contents(), count: frame.buffer.length)

                try core.setCFLSync(integrator == .cnab2 ? 0.05 : 1)
                try core.setReynoldsSync(1_000)
                let changed = try core.inspectSync()
                XCTAssertEqual(changed.step, before.step)
                XCTAssertEqual(changed.time, before.time)
                XCTAssertEqual(changed.initialTurnoverTime, before.initialTurnoverTime)
                XCTAssertEqual(changed.allocatedBytes, before.allocatedBytes)
                XCTAssertEqual(try core.spectrumSync(), spectrum)
                XCTAssertEqual(changed.reynolds, 1_000)

                let after = try core.advanceSync(steps: 1)
                XCTAssertEqual(after.step, before.step + 1)
                XCTAssertGreaterThan(after.time, before.time)
                if integrator == .cnab2 {
                    XCTAssertEqual(after.timeStep, before.timeStep,
                                   "CNAB2 must wait for its next controller checkpoint")
                } else {
                    XCTAssertLessThan(after.timeStep, before.timeStep * 0.5)
                }
                let evolved = try core.spectrumSync()[8 * n + 6]
                let expected = Double(spectrum[8 * n + 6].x)
                    * exp(-100 * (after.time - before.time) / 1_000)
                XCTAssertEqual(Double(evolved.x), expected, accuracy: 1e-4,
                               "Live viscosity must affect \(integrator), \(formulation)")
                XCTAssertEqual(Data(bytes: frame.buffer.contents(), count: frame.buffer.length), frameBytes)

                if integrator == .cnab2 {
                    let checkpoint = try core.advanceSync(steps: 95)
                    XCTAssertEqual(checkpoint.step, 100)
                    XCTAssertLessThan(checkpoint.timeStep, before.timeStep,
                                      "CNAB2 must apply the new CFL at step 100")
                }

                for invalid: Float in [0, -1, .nan, .infinity] {
                    XCTAssertThrowsError(try core.setCFLSync(invalid))
                }
                let latestStep = try core.inspectSync().step
                try core.setCFLSync(3)
                XCTAssertEqual(try core.advanceSync(steps: 1).step, latestStep + 1)
            }
        }
    }
}
