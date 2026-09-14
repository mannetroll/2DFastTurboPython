import Foundation

@main
enum FastTurboEntry {
    @MainActor
    static func main() {
        if CommandLine.arguments.contains("--benchmark") || CommandLine.arguments.contains("--help") {
            exit(BenchmarkCLI.run())
        }
        FastTurboApplication.run()
    }
}
