import AppKit
import Combine
import SwiftUI

/// The command-line entry point calls this only for GUI launches.
@MainActor
enum FastTurboApplication {
    private static var delegate: FastTurboAppDelegate?
    static func run() {
        let application = NSApplication.shared
        application.setActivationPolicy(.regular)
        let owner = FastTurboAppDelegate()
        delegate = owner
        application.delegate = owner
        application.run()
    }
}

@MainActor
final class FastTurboAppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate, NSMenuItemValidation {
    let model = SimulationController()
    private(set) var windowController: NSWindowController?
    private var diagnostics: FastTurboGUIDiagnostics?
    private var observers: [NSObjectProtocol] = []
    private var focusObservation: AnyCancellable?
    private var layoutIsFocused = false
    private var isAdjustingWindow = false
    private let launchedAt = ProcessInfo.processInfo.systemUptime
    private(set) var windowShownAt = 0.0

    func applicationDidFinishLaunching(_ notification: Notification) {
        installMenus()
        let window = FastTurboWindow(contentRect: NSRect(x: 0, y: 0, width: 1280, height: 900),
                              styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
                              backing: .buffered, defer: false)
        window.onTogglePause = { [weak self] in
            guard let self, self.model.isReady else { return }
            self.model.togglePause()
        }
        window.title = "2DFastTurbo"
        window.identifier = NSUserInterfaceItemIdentifier("FastTurboMainWindow")
        window.titleVisibility = .hidden
        window.titlebarAppearsTransparent = true
        window.isReleasedWhenClosed = false
        window.appearance = NSAppearance(named: .darkAqua)
        window.contentViewController = NSHostingController(rootView: FastTurboView(model: model))
        let restored = window.setFrameUsingName("FastTurboMainWindow")
        window.setFrameAutosaveName("FastTurboMainWindow")
        if !restored { window.center() }
        normalizeWindow(window)
        window.delegate = self
        windowController = NSWindowController(window: window)
        focusObservation = model.$isFocusMode.removeDuplicates().dropFirst().sink { [weak self] focused in
            // @Published emits before the value changes; resize after SwiftUI
            // receives the same mode, including changes made by its toolbar.
            DispatchQueue.main.async { [weak self] in
                guard let self, self.model.isFocusMode == focused else { return }
                self.resizeForFocus(focused)
            }
        }
        showMainWindow()
        windowShownAt = ProcessInfo.processInfo.systemUptime
        installVisibilityObservers()
        // Defer initialization until AppKit has had a chance to present the
        // retained window. FFT planning never blocks this main-loop turn.
        DispatchQueue.main.async { [weak self] in
            guard let self else { return }
            self.diagnostics = FastTurboGUIDiagnostics(application: self)
            self.diagnostics?.configureBeforeStart()
            self.model.start()
            self.diagnostics?.start(launchSeconds: self.windowShownAt - self.launchedAt)
        }
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        showMainWindow()
        return true
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }

    func applicationWillTerminate(_ notification: Notification) {
        model.setVisible(false)
        focusObservation = nil
        for observer in observers { NotificationCenter.default.removeObserver(observer) }
        observers.removeAll()
    }

    func showMainWindow() {
        guard let window = windowController?.window else { return }
        if !window.isVisible && windowShownAt > 0 {
            // A closed window can detach SwiftUI's Metal representable. Rehost
            // the view tree while preserving the controller and simulation.
            window.contentViewController = NSHostingController(rootView: FastTurboView(model: model))
        }
        if window.isMiniaturized { window.deminiaturize(nil) }
        normalizeWindow(window)
        windowController?.showWindow(nil)
        window.makeKeyAndOrderFront(nil)
        NSApplication.shared.activate(ignoringOtherApps: true)
        model.setVisible(true)
    }

    func windowWillClose(_ notification: Notification) { model.setVisible(false) }
    func windowDidMiniaturize(_ notification: Notification) { model.setVisible(false) }
    func windowDidDeminiaturize(_ notification: Notification) { model.setVisible(true) }
    func windowDidChangeOcclusionState(_ notification: Notification) {
        guard let window = windowController?.window else { return }
        model.setVisible(window.isVisible && !window.isMiniaturized && window.occlusionState.contains(.visible) && !NSApp.isHidden)
    }

    // The square is the resizable part; controls and window chrome have fixed
    // dimensions, so a constant window aspect ratio would distort the field.
    private func surroundingSize(_ window: NSWindow, focused: Bool) -> NSSize {
        window.contentView?.layoutSubtreeIfNeeded()
        return NSSize(
            width: TurboLayout.widthAroundCanvas(focus: focused)
                + max(0, window.frame.width - window.contentLayoutRect.width),
            height: TurboLayout.heightAroundCanvas(focus: focused)
                + max(0, window.frame.height - window.contentLayoutRect.height))
    }

    private func canvasLimits(_ window: NSWindow, surrounding: NSSize) -> ClosedRange<CGFloat> {
        let screen = (window.screen ?? NSScreen.main)?.visibleFrame
        let maximum = screen.map {
            max(1, min($0.width - surrounding.width, $0.height - surrounding.height))
        } ?? max(TurboLayout.minimumCanvasSide, 4096)
        return min(TurboLayout.minimumCanvasSide, maximum)...maximum
    }

    private func normalizeWindow(_ window: NSWindow) {
        guard !isAdjustingWindow else { return }
        let surrounding = surroundingSize(window, focused: layoutIsFocused)
        let side = min(window.frame.width - surrounding.width, window.frame.height - surrounding.height)
        resizeWindow(window, canvasSide: side)
    }

    private func resizeForFocus(_ focused: Bool) {
        guard focused != layoutIsFocused, let window = windowController?.window else { return }
        let oldSurrounding = surroundingSize(window, focused: layoutIsFocused)
        let side = min(window.frame.width - oldSurrounding.width, window.frame.height - oldSurrounding.height)
        layoutIsFocused = focused
        resizeWindow(window, canvasSide: side)
    }

    private func resizeWindow(_ window: NSWindow, canvasSide: CGFloat) {
        guard !isAdjustingWindow else { return }
        isAdjustingWindow = true
        defer { isAdjustingWindow = false }
        let surrounding = surroundingSize(window, focused: layoutIsFocused)
        let limits = canvasLimits(window, surrounding: surrounding)
        let side = min(max(canvasSide, limits.lowerBound), limits.upperBound)
        var frame = NSRect(x: window.frame.minX,
                           y: window.frame.maxY - side - surrounding.height,
                           width: side + surrounding.width,
                           height: side + surrounding.height)
        if let screen = (window.screen ?? NSScreen.main)?.visibleFrame {
            frame.origin.x = min(max(frame.minX, screen.minX), screen.maxX - frame.width)
            frame.origin.y = min(max(frame.minY, screen.minY), screen.maxY - frame.height)
        }
        window.minSize = NSSize(width: limits.lowerBound + surrounding.width,
                                height: limits.lowerBound + surrounding.height)
        window.maxSize = NSSize(width: limits.upperBound + surrounding.width,
                                height: limits.upperBound + surrounding.height)
        window.setFrame(frame, display: window.isVisible)
    }

    func windowWillResize(_ sender: NSWindow, to frameSize: NSSize) -> NSSize {
        let surrounding = surroundingSize(sender, focused: layoutIsFocused)
        let widthChange = abs(frameSize.width - sender.frame.width)
        let heightChange = abs(frameSize.height - sender.frame.height)
        let requestedSide = widthChange >= heightChange
            ? frameSize.width - surrounding.width : frameSize.height - surrounding.height
        let limits = canvasLimits(sender, surrounding: surrounding)
        let side = min(max(requestedSide, limits.lowerBound), limits.upperBound)
        return NSSize(width: side + surrounding.width, height: side + surrounding.height)
    }

    func windowWillUseStandardFrame(_ window: NSWindow, defaultFrame newFrame: NSRect) -> NSRect {
        let surrounding = surroundingSize(window, focused: layoutIsFocused)
        let limits = canvasLimits(window, surrounding: surrounding)
        let side = min(max(min(newFrame.width - surrounding.width, newFrame.height - surrounding.height),
                           limits.lowerBound), limits.upperBound)
        return NSRect(x: newFrame.minX, y: newFrame.maxY - side - surrounding.height,
                      width: side + surrounding.width, height: side + surrounding.height)
    }

    func windowDidChangeScreen(_ notification: Notification) {
        guard let window = notification.object as? NSWindow else { return }
        normalizeWindow(window)
    }

    func windowDidEndLiveResize(_ notification: Notification) {
        guard let window = notification.object as? NSWindow else { return }
        normalizeWindow(window)
    }

    private func installVisibilityObservers() {
        let center = NotificationCenter.default
        observers.append(center.addObserver(forName: NSApplication.didHideNotification, object: NSApp, queue: .main) { [weak self] _ in
            MainActor.assumeIsolated { self?.model.setVisible(false) }
        })
        observers.append(center.addObserver(forName: NSApplication.didUnhideNotification, object: NSApp, queue: .main) { [weak self] _ in
            MainActor.assumeIsolated { self?.showMainWindow() }
        })
    }

    private func item(_ title: String, _ action: Selector, _ key: String = "",
                      modifiers: NSEvent.ModifierFlags = [.command]) -> NSMenuItem {
        let item = NSMenuItem(title: title, action: action, keyEquivalent: key)
        item.target = self
        item.keyEquivalentModifierMask = modifiers
        return item
    }

    private func installMenus() {
        let main = NSMenu()
        let app = NSMenu(title: "2DFastTurbo")
        app.addItem(item("About 2DFastTurbo", #selector(showAbout)))
        app.addItem(.separator())
        let hide = NSMenuItem(title: "Hide 2DFastTurbo", action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
        app.addItem(hide)
        app.addItem(.separator())
        app.addItem(NSMenuItem(title: "Quit 2DFastTurbo", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q"))
        let appRoot = NSMenuItem(); appRoot.submenu = app; main.addItem(appRoot)

        let file = NSMenu(title: "File")
        file.addItem(item("Save Snapshot…", #selector(saveSnapshot), "s"))
        file.addItem(NSMenuItem(title: "Close Window", action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w"))
        let fileRoot = NSMenuItem(); fileRoot.submenu = file; main.addItem(fileRoot)

        let edit = NSMenu(title: "Edit")
        edit.addItem(NSMenuItem(title: "Undo", action: Selector(("undo:")), keyEquivalent: "z"))
        let redo = NSMenuItem(title: "Redo", action: Selector(("redo:")), keyEquivalent: "z")
        redo.keyEquivalentModifierMask = [.command, .shift]
        edit.addItem(redo)
        edit.addItem(.separator())
        edit.addItem(NSMenuItem(title: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x"))
        edit.addItem(NSMenuItem(title: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c"))
        edit.addItem(NSMenuItem(title: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v"))
        edit.addItem(NSMenuItem(title: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a"))
        let editRoot = NSMenuItem(); editRoot.submenu = edit; main.addItem(editRoot)

        let simulation = NSMenu(title: "Simulation")
        simulation.addItem(item("Pause", #selector(togglePause), " ", modifiers: []))
        simulation.addItem(item("Single Step", #selector(singleStep), String(UnicodeScalar(NSRightArrowFunctionKey)!), modifiers: []))
        simulation.addItem(.separator())
        simulation.addItem(item("Reset Flow", #selector(reset), "r", modifiers: []))
        simulation.addItem(item("New Initial Condition", #selector(newSeed), "r", modifiers: [.command, .shift]))
        let simulationRoot = NSMenuItem(); simulationRoot.submenu = simulation; main.addItem(simulationRoot)

        let view = NSMenu(title: "View")
        view.addItem(item("Focus on Flow", #selector(toggleFocus), "f", modifiers: []))
        view.addItem(item("Show Simulation", #selector(reopen)))
        let viewRoot = NSMenuItem(); viewRoot.submenu = view; main.addItem(viewRoot)

        let window = NSMenu(title: "Window")
        window.addItem(NSMenuItem(title: "Minimize", action: #selector(NSWindow.performMiniaturize(_:)), keyEquivalent: "m"))
        let windowRoot = NSMenuItem(); windowRoot.submenu = window; main.addItem(windowRoot)
        NSApp.windowsMenu = window
        NSApp.mainMenu = main
    }

    func validateMenuItem(_ menuItem: NSMenuItem) -> Bool {
        switch menuItem.action {
        case #selector(togglePause):
            menuItem.title = model.isRunning ? "Pause" : "Resume"
            return model.isReady
        case #selector(singleStep): return model.isReady
        case #selector(saveSnapshot): return model.isReady && !model.isExporting
        case #selector(toggleFocus):
            menuItem.title = model.isFocusMode ? "Show Controls" : "Focus on Flow"
            return true
        default: return true
        }
    }

    @objc private func togglePause() { model.togglePause() }
    @objc private func singleStep() { model.singleStep() }
    @objc private func reset() { model.reset() }
    @objc private func newSeed() { model.newSeed() }
    @objc private func toggleFocus() { model.isFocusMode.toggle() }
    @objc private func saveSnapshot() { model.saveSnapshot() }
    @objc private func reopen() { showMainWindow() }
    @objc private func showAbout() {
        let about = NSAlert()
        about.messageText = "2DFastTurbo"
        about.informativeText = "Version 1.0\n\n"
            + "GPU simulation of decaying, homogeneous 2D turbulence. "
            + "Fourier pseudospectral methods with 3/2-rule dealiasing "
            + "and VkFFT transforms. ETDRK4 time stepping by default.\n\n"
            + "Navier–Stokes equation"
        about.icon = NSApp.applicationIconImage
        let content = NSView(frame: NSRect(x: 0, y: 0, width: 360, height: 70))
        if let source = NSImage(named: "NavierStokes") {
            // Content bounds of the transparent asset, in AppKit's bottom-left coordinates.
            let equation = NSImage(size: NSSize(width: 1990, height: 380), flipped: false) { rect in
                source.draw(in: rect, from: NSRect(x: 78, y: 186, width: 1990, height: 380),
                            operation: .sourceOver, fraction: 1)
                return true
            }
            equation.isTemplate = true
            let image = NSImageView(frame: NSRect(x: 20, y: 4, width: 320, height: 61))
            image.image = equation
            image.contentTintColor = .labelColor
            image.imageScaling = .scaleProportionallyUpOrDown
            image.setAccessibilityLabel("Navier–Stokes equation: ρ(∂v/∂t + v·∇v) = −∇p + μ∇²v")
            content.addSubview(image)
        } else {
            let equation = NSTextField(labelWithString: "ρ(∂v/∂t + v·∇v) = −∇p + μ∇²v")
            equation.frame = NSRect(x: 0, y: 23, width: 360, height: 24)
            equation.alignment = .center
            content.addSubview(equation)
        }
        about.accessoryView = content
        about.addButton(withTitle: "OK")
        about.window.title = "About 2DFastTurbo"
        about.window.appearance = NSAppearance(named: .darkAqua)
        about.runModal()
    }
}
