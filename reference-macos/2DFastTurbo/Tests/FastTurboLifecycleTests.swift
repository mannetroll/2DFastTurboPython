import Foundation
import Metal
import XCTest

final class FastTurboLifecycleTests: XCTestCase {
    private func device() throws -> MTLDevice {
        guard let device = MTLCreateSystemDefaultDevice() else {
            throw XCTSkip("Metal device access is required for GPU lifecycle tests.")
        }
        return device
    }

    private func configuration() -> FastTurboConfiguration {
        var configuration = FastTurboConfiguration()
        configuration.resolution = 32
        configuration.fixedTimeStep = 0.002
        return configuration
    }

    private func contents(_ frame: FastTurboFrame) -> Data {
        Data(bytes: frame.buffer.contents(), count: 3 * frame.gridSize * frame.gridSize * MemoryLayout<Float>.stride)
    }

    func testInvalidConfigurationFailsBeforeAllocatingSolverState() throws {
        let device = try device()
        let invalid: [(inout FastTurboConfiguration) -> Void] = [
            { $0.resolution = 31 },
            { $0.resolution = 8 },
            { $0.reynolds = 0 },
            { $0.reynolds = .nan },
            { $0.cfl = 0 },
            { $0.cfl = .infinity },
            { $0.fixedTimeStep = 0 },
            { $0.fixedTimeStep = .nan },
        ]
        for change in invalid {
            var configuration = configuration()
            change(&configuration)
            XCTAssertThrowsError(try FastTurboCore(configuration: configuration, device: device)) { error in
                guard case FastTurboError.invalidConfiguration = error else {
                    XCTFail("Expected a configuration error, received \(error)")
                    return
                }
            }
        }
    }

    func testSnapshotSlotsRemainImmutableUntilTheirLeasesAreReleased() throws {
        let core = try FastTurboCore(configuration: configuration(), device: device())
        var frames: [FastTurboFrame?] = try (0..<3).map { _ in
            try XCTUnwrap(core.snapshotSync(includeVelocity: true))
        }
        let firstContents = contents(try XCTUnwrap(frames[0]))
        let firstTime = try XCTUnwrap(frames[0]).time
        try withExtendedLifetime(frames) {
            XCTAssertNil(try core.snapshotSync(includeVelocity: true), "A busy snapshot must be coalesced, not overwritten.")
        }

        _ = try core.advanceSync(steps: 8)
        XCTAssertEqual(contents(try XCTUnwrap(frames[0])), firstContents)
        XCTAssertEqual(try XCTUnwrap(frames[0]).time, firstTime)

        frames[1] = nil
        frames[1] = try XCTUnwrap(core.snapshotSync(includeVelocity: true))
        XCTAssertGreaterThan(try XCTUnwrap(frames[1]).time, firstTime)
        XCTAssertEqual(contents(try XCTUnwrap(frames[0])), firstContents)
        try withExtendedLifetime(frames) { XCTAssertNil(try core.snapshotSync(includeVelocity: true)) }

        // Reset changes simulation buffers while the renderer may retain an
        // earlier frame. It must not mutate that frame's storage or timestamp.
        try core.resetSync()
        XCTAssertEqual(contents(try XCTUnwrap(frames[0])), firstContents)
        XCTAssertEqual(try core.inspectSync().step, 0)
        XCTAssertEqual(try core.inspectSync().time, 0)

        frames.removeAll()
        let reset = try XCTUnwrap(core.snapshotSync(includeVelocity: true))
        XCTAssertEqual(reset.time, 0)
        XCTAssertEqual(contents(reset), firstContents)
    }

    func testZeroStepAndSingleStepHaveCompletedTimeAccounting() throws {
        let core = try FastTurboCore(configuration: configuration(), device: device())
        let initial = try core.inspectSync()
        let unchanged = try core.advanceSync(steps: 0)
        XCTAssertEqual(unchanged.step, initial.step)
        XCTAssertEqual(unchanged.time, initial.time)
        let completed = try core.advanceSync(steps: 1)
        XCTAssertEqual(completed.step, 1)
        XCTAssertEqual(completed.time, 0.002, accuracy: 1e-9)
        let frame = try XCTUnwrap(core.snapshotSync(includeVelocity: true))
        XCTAssertEqual(frame.time, completed.time, accuracy: 1e-12)
        let inspection = try core.inspectSync()
        XCTAssertEqual(inspection.step, completed.step)
        XCTAssertEqual(inspection.time, completed.time)
        XCTAssertTrue(inspection.isFinite)
    }

    func testAsyncCompletionDescribesCommittedGPUState() throws {
        let core = try FastTurboCore(configuration: configuration(), device: device())
        let completion = expectation(description: "GPU batch has completed")
        core.advance(steps: 7) { result in
            switch result {
            case .success(let statistics):
                XCTAssertEqual(statistics.step, 7)
                XCTAssertEqual(statistics.time, 0.014, accuracy: 1e-8)
                XCTAssertTrue(statistics.isFinite)
            case .failure(let error):
                XCTFail("GPU batch failed: \(error)")
            }
            completion.fulfill()
        }
        wait(for: [completion], timeout: 10)
        let inspection = try core.inspectSync()
        XCTAssertEqual(inspection.step, 7)
        let frame = try XCTUnwrap(core.snapshotSync(includeVelocity: true))
        XCTAssertEqual(frame.time, inspection.time)
    }
}
