import AppKit

/// Space controls transport even after a SwiftUI picker, slider or button has
/// become first responder. A menu key equivalent alone does not ensure that.
@MainActor
final class FastTurboWindow: NSWindow {
    var onTogglePause: (() -> Void)?

    override func performKeyEquivalent(with event: NSEvent) -> Bool {
        if handleTransportKey(event) { return true }
        return super.performKeyEquivalent(with: event)
    }

    override func sendEvent(_ event: NSEvent) {
        if handleTransportKey(event) { return }
        super.sendEvent(event)
    }

    private func handleTransportKey(_ event: NSEvent) -> Bool {
        guard event.type == .keyDown,
              event.charactersIgnoringModifiers == " ",
              event.modifierFlags.intersection([.shift, .control, .option, .command]).isEmpty,
              attachedSheet == nil,
              let onTogglePause else { return false }

        // The only main-window text entry is the numeric seed, where Space is
        // not input. Keep transport available there too. Dialogs keep their
        // normal keyboard handling, including spaces in snapshot filenames.
        if !event.isARepeat { onTogglePause() }
        return true
    }
}
