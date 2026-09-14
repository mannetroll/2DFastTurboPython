import SwiftUI

// The window sizes itself around the complete square simulation domain.
enum TurboLayout {
    static let horizontalPadding: CGFloat = 22
    static let inspectorWidth: CGFloat = 196
    static let panelSpacing: CGFloat = 18
    static let headerHeight: CGFloat = 77
    static let fieldHeaderHeight: CGFloat = 34
    static let statsHeight: CGFloat = 54
    static let statsSpacing: CGFloat = 8
    static let bottomPadding: CGFloat = 12
    static let minimumCanvasSide: CGFloat = 576

    static func widthAroundCanvas(focus: Bool) -> CGFloat {
        horizontalPadding * 2 + (focus ? 0 : inspectorWidth + panelSpacing)
    }

    static func heightAroundCanvas(focus: Bool) -> CGFloat {
        headerHeight + fieldHeaderHeight + bottomPadding + (focus ? 0 : statsSpacing + statsHeight)
    }
}

private enum TurboTheme {
    static let background = Color(red: 0.035, green: 0.047, blue: 0.073)
    static let panel = Color(red: 0.061, green: 0.076, blue: 0.106)
    static let secondary = Color(red: 0.48, green: 0.55, blue: 0.65)
    static let accent = Color(red: 0.44, green: 0.91, blue: 0.82)
    static let border = Color.white.opacity(0.075)
}

struct FastTurboView: View {
    @ObservedObject var model: SimulationController
    @State private var seedText = "2"

    private var integratorSelection: Binding<Integrator> {
        Binding(get: { model.configuration.integrator }, set: { method in
            var configuration = model.configuration
            configuration.integrator = method
            if method != .etdrk4 { configuration.cfl = min(configuration.cfl, 1.5) }
            model.configuration = configuration
        })
    }

    var body: some View {
        VStack(spacing: 0) {
            header
            HStack(alignment: .top, spacing: TurboLayout.panelSpacing) {
                simulationPanel.frame(maxWidth: .infinity, maxHeight: .infinity)
                if !model.isFocusMode { inspector.frame(width: TurboLayout.inspectorWidth) }
            }
            .padding(.horizontal, TurboLayout.horizontalPadding)
            .padding(.bottom, model.isFocusMode ? TurboLayout.bottomPadding : TurboLayout.statsSpacing)
            if !model.isFocusMode {
                metrics
                    .padding(.horizontal, TurboLayout.horizontalPadding)
                    .padding(.bottom, TurboLayout.bottomPadding)
            }
        }
        .background(TurboTheme.background)
        .foregroundStyle(Color.white.opacity(0.9))
        .tint(TurboTheme.accent)
        .preferredColorScheme(.dark)
        .onAppear { seedText = String(model.configuration.seed) }
        .onChange(of: model.configuration.seed) { _, seed in seedText = String(seed) }
    }

    private var header: some View {
        HStack(spacing: 13) {
            ZStack {
                Circle().stroke(TurboTheme.accent.opacity(0.23), lineWidth: 1)
                Image(systemName: "hurricane").font(.system(size: 23, weight: .light))
            }
            .foregroundStyle(TurboTheme.accent)
            .frame(width: 42, height: 42)
            VStack(alignment: .leading, spacing: 4) {
                Text("2D FAST TURBO").font(.system(size: 16, weight: .semibold, design: .rounded)).tracking(2.6)
                Text("DECAYING TURBULENCE IN HDR").font(.system(size: 8, weight: .medium, design: .monospaced)).tracking(2)
                    .foregroundStyle(TurboTheme.secondary)
            }
            Spacer(minLength: 10)
            HStack(spacing: 6) {
                Circle().fill(model.isReady ? TurboTheme.accent : TurboTheme.secondary).frame(width: 5, height: 5)
                Text("METAL / VkFFT").font(.system(size: 9, weight: .medium, design: .monospaced))
            }
            .foregroundStyle(TurboTheme.accent)
            .padding(.horizontal, 10).padding(.vertical, 8)
            .background(TurboTheme.accent.opacity(0.065), in: Capsule())
            .help(model.deviceName)
            iconButton(model.isFocusMode ? "arrow.down.right.and.arrow.up.left" : "arrow.up.left.and.arrow.down.right", "Focus mode · F") {
                model.isFocusMode.toggle()
            }
            iconButton("square.and.arrow.up", "Save PNG · ⌘S") { model.saveSnapshot() }
                .disabled(!model.isReady || model.isExporting)
        }
        .padding(.horizontal, 24)
        .frame(height: TurboLayout.headerHeight)
    }

    private var simulationPanel: some View {
        VStack(spacing: 0) {
            HStack {
                Circle().fill(model.isReady && model.isRunning ? TurboTheme.accent : TurboTheme.secondary).frame(width: 5, height: 5)
                Text(model.display.title.uppercased()).font(.system(size: 10, weight: .medium, design: .monospaced)).tracking(1.5)
                Spacer()
                colorLegend.padding(.trailing, 12)
                Text("\(model.configuration.resolution)² · \(model.configuration.paddedResolution)² padded")
                    .font(.system(size: 10, design: .monospaced)).foregroundStyle(TurboTheme.secondary)
            }
            .padding(.horizontal, 14).frame(height: TurboLayout.fieldHeaderHeight)
            ZStack {
                FastTurboCanvas(source: model.frames, palette: model.palette, display: model.display,
                                exposure: model.exposure, vorticitySigma: model.vorticitySigma,
                                vorticityColorRange: model.vorticityColorRange, showFlowLines: model.showFlowLines,
                                onError: { model.statusMessage = $0 }, onPresented: model.recordPresentation)
                    .accessibilityLabel("Decaying turbulence: \(model.display.title)")
                if !model.isReady {
                    VStack(spacing: 13) {
                        if model.statusMessage == nil { ProgressView().controlSize(.small) }
                        else { Image(systemName: "exclamationmark.circle").font(.system(size: 24, weight: .light)) }
                        Text(model.statusMessage == nil ? "Preparing the flow" : "Unable to start the flow")
                            .font(.system(size: 12, weight: .medium))
                        Text(model.statusMessage == nil ? "The simulation will appear here when ready." : "See the message below.")
                            .font(.system(size: 10)).foregroundStyle(TurboTheme.secondary)
                    }
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    .background(TurboTheme.background.opacity(0.94))
                }
                VStack {
                    HStack {
                        Text("UNFORCED · SEED \(model.configuration.seed)")
                            .font(.system(size: 8, weight: .medium, design: .monospaced)).tracking(1)
                            .padding(.horizontal, 10).padding(.vertical, 7)
                            .background(.black.opacity(0.3), in: Capsule())
                        Spacer()
                    }
                    Spacer()
                    if let message = model.statusMessage {
                        HStack(alignment: .top, spacing: 8) {
                            Image(systemName: "info.circle")
                            Text(message).textSelection(.enabled)
                            Spacer(minLength: 0)
                            Button { model.statusMessage = nil } label: { Image(systemName: "xmark") }
                                .buttonStyle(.plain).help("Dismiss message")
                        }
                        .font(.system(size: 11)).padding(12)
                        .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 10))
                    }
                    HStack {
                        HStack(spacing: 0) {
                            transportButton(model.isRunning ? "pause.fill" : "play.fill", "Pause / resume · Space") { model.togglePause() }
                            transportButton("forward.frame.fill", "Single step · →") { model.singleStep() }
                            transportButton("arrow.counterclockwise", "Reset the same initial condition · R") { model.reset() }
                        }
                        .background(.ultraThinMaterial, in: Capsule())
                        .overlay(Capsule().stroke(.white.opacity(0.12)))
                        Spacer()
                        Text(String(format: "t  %.3f", model.time)).monospacedDigit()
                            .font(.system(size: 10, design: .monospaced))
                            .padding(.horizontal, 11).padding(.vertical, 8)
                            .background(.black.opacity(0.3), in: Capsule())
                    }
                }
                .padding(14)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .clipped()
        }
        .background(TurboTheme.panel.opacity(0.42), in: RoundedRectangle(cornerRadius: 15))
        .clipShape(RoundedRectangle(cornerRadius: 15))
        .overlay(RoundedRectangle(cornerRadius: 15).stroke(TurboTheme.border))
    }

    private var colorLegend: some View {
        HStack(spacing: 5) {
            Text(model.display == .vorticity ? "−ω" : "0")
            RoundedRectangle(cornerRadius: 2)
                .fill(LinearGradient(colors: model.palette.colors, startPoint: .leading, endPoint: .trailing))
                .frame(width: 54, height: 4)
            Text(model.display == .vorticity ? "+ω" : model.display == .enstrophy ? "ω²" : "|u|")
        }
        .font(.system(size: 8, design: .monospaced)).foregroundStyle(TurboTheme.secondary)
    }

    private var metrics: some View {
        HStack(spacing: 0) {
            metric("SIMULATED TIME", number(model.performance.simulationTimePerSecond), unit: "per wall s", prominent: true)
                .frame(width: 110)
                .help("Average since this run started: completed simulation advance divided by active running wall time. Pauses and manual steps are excluded; retained while paused.")
            metricDivider
            metric("INITIAL TURNOVERS", number(model.performance.initialTurnoversPerSecond), unit: "per wall s", prominent: true)
                .frame(width: 110)
                .help("Average throughput since this run started, divided by the initial large-eddy turnover time. Retained while paused.")
            metricDivider
            VStack(alignment: .leading, spacing: 5) {
                metricLabel("KINETIC ENERGY")
                HStack(spacing: 4) {
                    metricValue(number(model.energy))
                    EnergySparkline(samples: model.history)
                        .stroke(TurboTheme.accent.opacity(0.85), style: StrokeStyle(lineWidth: 1.1, lineCap: .round))
                        .frame(width: 24, height: 16).accessibilityLabel("Kinetic energy history")
                }
            }.frame(maxWidth: .infinity, alignment: .leading)
            metricDivider
            metric("ENSTROPHY", number(model.enstrophy))
            metricDivider
            metric("TIMESTEP", number(model.timeStep), unit: "Δt")
            metricDivider
            metric("INTEGRATION", String(format: "%.0f", model.performance.stepsPerSecond), unit: "steps/s")
                .help("Last measured simulation steps per wall-clock second. Retained while paused.")
            metricDivider
            metric("STEP", "\(model.step)")
            metricDivider
            metric("GPU ALLOCATION", String(format: "%.0f", Double(model.allocatedBytes) / 1048576), unit: "MiB")
                .help("Current Metal resource allocation, including FFT plans and rendering.")
        }
        .padding(.horizontal, 14).frame(height: TurboLayout.statsHeight)
        .background(TurboTheme.panel.opacity(0.42), in: RoundedRectangle(cornerRadius: 10))
        .overlay(RoundedRectangle(cornerRadius: 10).stroke(TurboTheme.border))
    }

    private var inspector: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                compactChoice("k₀") {
                    Picker("Initial wavenumber", selection: $model.configuration.initialWaveNumber) {
                        ForEach([2, 4, 6, 8, 10, 12, 16, 20], id: \.self) { Text("\($0)").tag($0) }
                    }
                }
                compactChoice("Seed") {
                    HStack(spacing: 6) {
                        TextField("Seed", text: $seedText)
                            .font(.system(size: 11, design: .monospaced)).multilineTextAlignment(.trailing)
                            .textFieldStyle(.roundedBorder)
                            .onSubmit {
                                if let seed = UInt64(seedText) { model.configuration.seed = seed }
                                else { seedText = String(model.configuration.seed) }
                            }
                            .help(String(model.configuration.seed))
                        Button { model.newSeed() } label: { Image(systemName: "dice") }
                            .buttonStyle(.borderless).help("New seed · ⇧⌘R")
                    }
                }
                compactChoice("N") {
                    Picker("Resolution", selection: $model.configuration.resolution) {
                        ForEach([128, 256, 512, 1024, 2048, 4096], id: \.self) { Text("\($0)").tag($0) }
                    }
                }
                ReynoldsControls(controls: model.liveControls,
                                 setReynolds: model.setLogarithmicReynolds,
                                 setRegulation: { model.isPalinstrophyRegulated = $0 },
                                 finishEditing: model.flushLiveParameters)
                Divider().overlay(TurboTheme.border)
                VStack(spacing: 4) {
                    ForEach(TurboField.allCases) { field in
                        Button { model.display = field } label: {
                            HStack {
                                Text(field.title).font(.system(size: 11, weight: .medium))
                                Spacer()
                                Image(systemName: model.display == field ? "checkmark.circle.fill" : "circle")
                                    .font(.system(size: 10))
                            }
                            .foregroundStyle(model.display == field ? TurboTheme.accent : TurboTheme.secondary)
                            .padding(.horizontal, 8).padding(.vertical, 5)
                            .background(.white.opacity(model.display == field ? 0.055 : 0.015), in: RoundedRectangle(cornerRadius: 6))
                            .overlay(RoundedRectangle(cornerRadius: 6).stroke(model.display == field ? TurboTheme.accent.opacity(0.6) : TurboTheme.border))
                        }
                        .buttonStyle(.plain).accessibilityAddTraits(model.display == field ? .isSelected : [])
                    }
                }
                VStack(spacing: 5) {
                    ForEach(TurboPalette.allCases) { palette in
                        Button { model.palette = palette } label: {
                            HStack(spacing: 8) {
                                Text(palette.title).font(.system(size: 9, weight: .medium))
                                    .foregroundStyle(model.palette == palette ? .white : TurboTheme.secondary)
                                    .frame(width: 42, alignment: .leading)
                                RoundedRectangle(cornerRadius: 3)
                                    .fill(LinearGradient(colors: palette.colors, startPoint: .leading, endPoint: .trailing))
                                    .frame(height: 12)
                            }
                            .padding(.horizontal, 8).padding(.vertical, 6).frame(maxWidth: .infinity)
                            .background(.white.opacity(model.palette == palette ? 0.055 : 0.015), in: RoundedRectangle(cornerRadius: 6))
                            .overlay(RoundedRectangle(cornerRadius: 6).stroke(model.palette == palette ? TurboTheme.accent.opacity(0.6) : TurboTheme.border))
                        }
                        .buttonStyle(.plain).accessibilityLabel("\(palette.title) palette")
                        .accessibilityAddTraits(model.palette == palette ? .isSelected : [])
                    }
                }
                VStack(spacing: 5) {
                    HStack {
                        if model.display == .vorticity {
                            Picker("Color range", selection: $model.vorticityColorRange) {
                                ForEach(TurboColorRange.allCases) { range in
                                    Text(range.title).tag(range)
                                }
                            }
                            .labelsHidden().pickerStyle(.menu).controlSize(.mini).fixedSize()
                            .accessibilityLabel("Vorticity color range mode")
                            .help("Sigma clips at the selected standard deviations around the mean. Exposure adjusts soft contrast.")
                        } else {
                            Text("Exposure").font(.system(size: 11))
                        }
                        Spacer()
                        Text(model.display == .vorticity && model.vorticityColorRange == .sigma
                             ? String(format: "±%.1fσ", model.vorticitySigma)
                             : String(format: "%.1f×", model.exposure))
                            .font(.system(size: 10, design: .monospaced))
                            .foregroundStyle(TurboTheme.accent)
                    }
                    if model.display == .vorticity && model.vorticityColorRange == .sigma {
                        Slider(value: $model.vorticitySigma, in: 1.5...3.5).controlSize(.mini)
                            .accessibilityLabel("Vorticity sigma range")
                    } else {
                        Slider(value: $model.exposure, in: 0.25...3).controlSize(.mini).accessibilityLabel("Exposure")
                    }
                }
                Toggle("Velocity direction", isOn: $model.showFlowLines).font(.system(size: 11))
                    .toggleStyle(.switch).controlSize(.mini)
                Divider().overlay(TurboTheme.border)
                Text("NUMERICS").font(.system(size: 9, weight: .medium, design: .monospaced))
                    .tracking(0.5).foregroundStyle(TurboTheme.secondary)
                compactChoice("Method") {
                    Picker("Integrator", selection: integratorSelection) {
                        ForEach(Integrator.allCases, id: \.self) { Text($0.displayTitle).tag($0) }
                    }
                }
                compactChoice("Form") {
                    Picker("Nonlinear formulation", selection: $model.configuration.nonlinearFormulation) {
                        Text("Stress").tag(NonlinearFormulation.stress)
                        Text("Advective").tag(NonlinearFormulation.advective)
                    }
                    .help("Nonlinear form on the 3/2-padded grid.")
                }
                compactChoice("CFL") {
                    CFLControls(controls: model.liveControls, setCFL: model.setCFL,
                                finishEditing: model.flushLiveParameters)
                }
            }
            .padding(14)
        }
        .scrollIndicators(.automatic)
        .background(TurboTheme.panel.opacity(0.68), in: RoundedRectangle(cornerRadius: 15))
        .overlay(RoundedRectangle(cornerRadius: 15).stroke(TurboTheme.border))
    }

    private func number(_ value: Double) -> String {
        guard model.isReady, value.isFinite else { return "—" }
        if value != 0 && (abs(value) < 0.001 || abs(value) >= 10000) { return String(format: "%.2e", value) }
        return String(format: "%.3f", value)
    }

    private func metric(_ title: String, _ value: String, unit: String = "", prominent: Bool = false) -> some View {
        VStack(alignment: .leading, spacing: 5) {
            metricLabel(title)
            HStack(alignment: .firstTextBaseline, spacing: 3) {
                metricValue(value, prominent: prominent)
                if !unit.isEmpty {
                    Text(unit).font(.system(size: 7.5, design: .monospaced))
                        .foregroundStyle(TurboTheme.secondary).fixedSize()
                }
            }
        }.frame(maxWidth: .infinity, alignment: .leading)
    }

    private func metricLabel(_ title: String) -> some View {
        Text(title).font(.system(size: 7, weight: .medium, design: .monospaced)).tracking(0.5)
            .foregroundStyle(TurboTheme.secondary).lineLimit(1)
    }

    private func metricValue(_ value: String, prominent: Bool = false) -> some View {
        Text(value).font(.system(size: 16, weight: .light, design: .monospaced))
            .foregroundStyle(prominent ? TurboTheme.accent : .white.opacity(0.9)).monospacedDigit()
            .lineLimit(1).minimumScaleFactor(0.75)
    }

    private var metricDivider: some View {
        Rectangle().fill(TurboTheme.border).frame(width: 1, height: 26).padding(.horizontal, 5)
    }

    private func compactChoice<Content: View>(_ title: String, @ViewBuilder content: () -> Content) -> some View {
        HStack(spacing: 6) {
            Text(title).font(.system(size: 10, design: .monospaced)).fixedSize()
                .frame(width: 40, alignment: .leading)
            content().labelsHidden().controlSize(.small).frame(maxWidth: .infinity)
        }
    }

    private func iconButton(_ symbol: String, _ help: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: symbol).font(.system(size: 13)).foregroundStyle(.white.opacity(0.65))
                .frame(width: 32, height: 32).background(.white.opacity(0.035), in: RoundedRectangle(cornerRadius: 8))
        }.buttonStyle(.plain).help(help).accessibilityLabel(help)
    }

    private func transportButton(_ symbol: String, _ help: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: symbol).font(.system(size: 12, weight: .semibold)).frame(width: 38, height: 35)
        }.buttonStyle(.plain).help(help).disabled(!model.isReady)
    }
}

private struct ReynoldsControls: View {
    @ObservedObject var controls: LiveControlDisplay
    let setReynolds: (Double) -> Void
    let setRegulation: (Bool) -> Void
    let finishEditing: () -> Void

    var body: some View {
        let values = controls.values
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 4) {
                HStack(spacing: 4) {
                    Text("Re").font(.system(size: 10, design: .monospaced))
                    Text(String(values.reynolds))
                        .font(.system(size: 11, design: .monospaced))
                        .monospacedDigit()
                        .foregroundStyle(TurboTheme.accent)
                }
                .frame(width: 90, alignment: .leading)
                .accessibilityElement(children: .ignore)
                .accessibilityLabel("Reynolds number \(values.reynolds)")
                Spacer()
                Text(values.isPalinstrophyRegulated ? "AUTO" : "MANUAL")
                    .font(.system(size: 8, weight: .medium, design: .monospaced))
                    .foregroundStyle(TurboTheme.secondary)
            }
            // A step creates thousands of native tick marks. AppKit rebuilds
            // their geometry on each automatic Re update, blocking frame delivery.
            Slider(value: Binding(get: { controls.values.logarithmicReynolds }, set: setReynolds),
                   in: 3...6,
                   onEditingChanged: { if !$0 { finishEditing() } })
                .controlSize(.mini)
                .disabled(values.isPalinstrophyRegulated)
                .accessibilityLabel("Reynolds number")
            Toggle("Regulate with palinstrophy",
                   isOn: Binding(get: { controls.values.isPalinstrophyRegulated }, set: setRegulation))
                .font(.system(size: 10))
                .toggleStyle(.switch)
                .controlSize(.mini)
                .help("Automatically adjusts Reynolds number from 10,000·palinstrophy/(enstrophy·kmax²).")
            Text(values.palinstrophy)
                .font(.system(size: 10, design: .monospaced))
                .foregroundStyle(.white)
        }
        .transaction { $0.animation = nil }
    }
}

private struct CFLControls: View {
    @ObservedObject var controls: LiveControlDisplay
    let setCFL: (Double) -> Void
    let finishEditing: () -> Void

    var body: some View {
        VStack(alignment: .trailing, spacing: 5) {
            Text(String(format: "%.2f", controls.values.cfl))
                .font(.system(size: 11, design: .monospaced))
                .monospacedDigit()
                .foregroundStyle(TurboTheme.accent)
            Slider(value: Binding(get: { Double(controls.values.cfl) }, set: setCFL),
                   in: 1...3,
                   onEditingChanged: { if !$0 { finishEditing() } })
                .controlSize(.mini)
                .accessibilityLabel("CFL")
                .help("Adjust the adaptive timestep without restarting the simulation.")
        }
        .transaction { $0.animation = nil }
    }
}

private struct EnergySparkline: Shape {
    var samples: [Double]
    func path(in rect: CGRect) -> Path {
        let values = Array(samples.filter(\.isFinite).suffix(120))
        guard values.count > 1, let low = values.min(), let high = values.max() else { return Path() }
        let spread = max(high - low, max(abs(high), 1e-12) * 0.02)
        let middle = (high + low) * 0.5
        var path = Path()
        for (index, value) in values.enumerated() {
            let point = CGPoint(x: rect.minX + CGFloat(index) / CGFloat(values.count - 1) * rect.width,
                                y: rect.midY - CGFloat((value - middle) / spread) * rect.height * 0.85)
            if index == 0 { path.move(to: point) } else { path.addLine(to: point) }
        }
        return path
    }
}
