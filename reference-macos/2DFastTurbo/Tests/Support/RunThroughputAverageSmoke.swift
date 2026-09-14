import Foundation

/// CPU-only: compile with FastTurboTypes.swift and PresentationTypes.swift.
@main
enum RunThroughputAverageSmoke {
    static func main() {
        var average = RunThroughputAverage()
        func check(_ actual: Double, _ expected: Double, _ reason: String) {
            precondition(abs(actual - expected) < 1e-12, reason)
        }
        check(average.rate(at: 10), 0, "Preparation has no running throughput")
        average.beginBatch(automatic: true, at: 10)
        average.completeBatch(from: 0, to: 2, automatic: true)
        check(average.rate(at: 11), 2, "The first automatic batch contributes")
        average.beginBatch(automatic: true, at: 14)
        average.completeBatch(from: 2, to: 6, automatic: true)
        check(average.rate(at: 15), 1.2, "Use total advance/time, including gaps, not an average of samples")
        average.stop(at: 15)
        check(average.rate(at: 90), 1.2, "Paused idle time cannot dilute the average")
        average.beginBatch(automatic: false, at: 100)
        average.completeBatch(from: 6, to: 106, automatic: false)
        check(average.wallTime(at: 150), 5, "Manual work contributes no running wall time")
        check(average.rate(at: 150), 1.2, "Manual advance cannot inflate throughput")
        average.beginBatch(automatic: true, at: 200)
        average.completeBatch(from: 106, to: 108, automatic: true)
        check(average.rate(at: 203), 1, "Resume continues the original weighted average")
        average.completeBatch(from: 108, to: 109, automatic: true)
        average.stop(at: 206)
        check(average.rate(at: 300), 9.0 / 11.0, "An automatic batch draining at pause counts once")
        average.stop(at: 400)
        check(average.rate(at: 400), 9.0 / 11.0, "Repeated idle notifications cannot change history")

        average = RunThroughputAverage()
        average.beginBatch(automatic: false, at: 500)
        average.completeBatch(from: 0, to: 1, automatic: false)
        check(average.rate(at: 600), 0, "Reset clears history, including when followed by a manual step")
        average.beginBatch(automatic: true, at: 700)
        average.completeBatch(from: 1, to: 1.5, automatic: true)
        check(average.rate(at: 701), 0.5, "First automatic work after manual initialization counts exactly")
        print("PASS: whole-run weighted average, initial batch, pause/drain, manual steps, resume, reset")
    }
}
