import AppKit
import CoreGraphics
import MetalKit
import SwiftUI

private enum TurboOutput {
    case sdr, hdr

    var pixelFormat: MTLPixelFormat { self == .hdr ? .rgba16Float : .bgra8Unorm }
}

private enum CanvasError: LocalizedError {
    case unavailable(String)
    var errorDescription: String? {
        switch self { case .unavailable(let message): return message }
    }
}

// Keep this layout in sync with TurboUniforms in TurboRendering.metal (36 bytes).
private struct TurboUniforms {
    var gridSize: UInt32 = 1
    var palette: UInt32 = 0
    var display: UInt32 = 0
    var showFlowLines: UInt32 = 0
    var exposure: Float = 1
    var vorticityScale: Float = 1
    var speedScale: Float = 1
    var vorticityMean: Float = 0
    var vorticityColorRange: UInt32 = 0

    init(frame: FastTurboFrame, palette: TurboPalette, display: TurboField,
         exposure: Double, vorticitySigma: Double, vorticityColorRange: TurboColorRange,
         showFlowLines: Bool, size _: CGSize) {
        gridSize = UInt32(frame.gridSize)
        self.palette = palette.shaderIndex
        self.display = display.shaderIndex
        self.exposure = Float(exposure)
        self.showFlowLines = showFlowLines ? 1 : 0
        self.vorticityColorRange = vorticityColorRange.shaderIndex
        vorticityScale = display == .vorticity && vorticityColorRange == .sigma
            ? max(Float(vorticitySigma) * frame.standardDeviationVorticity, 1e-12)
            : max(frame.maxVorticity, 0.0001)
        vorticityMean = frame.meanVorticity
        speedScale = max(frame.maxSpeed, 0.0001)
    }
}

private final class TurboPipeline {
    let device: any MTLDevice
    let queue: any MTLCommandQueue
    let field: any MTLRenderPipelineState
    let flow: any MTLRenderPipelineState
    init(device: any MTLDevice, output: TurboOutput = .sdr) throws {
        self.device = device
        guard let queue = device.makeCommandQueue() else {
            throw CanvasError.unavailable("Metal could not create a rendering command queue.")
        }
        self.queue = queue
        guard let library = device.makeDefaultLibrary(),
              let fieldVertex = library.makeFunction(name: "turboFieldVertex"),
              let fieldFragment = try Self.outputFunction(library, name: "turboFieldFragment", output: output),
              let flowVertex = library.makeFunction(name: "turboFlowVertex"),
              let flowFragment = try Self.outputFunction(library, name: "turboFlowFragment", output: output) else {
            throw CanvasError.unavailable("The turbulence shaders are missing. Rebuild 2DFastTurbo to compile TurboRendering.metal.")
        }
        let descriptor = MTLRenderPipelineDescriptor()
        descriptor.label = "FastTurbo · vorticity field"
        descriptor.vertexFunction = fieldVertex
        descriptor.fragmentFunction = fieldFragment
        descriptor.colorAttachments[0].pixelFormat = output.pixelFormat
        field = try device.makeRenderPipelineState(descriptor: descriptor)

        descriptor.label = "FastTurbo · velocity streamlines"
        descriptor.vertexFunction = flowVertex
        descriptor.fragmentFunction = flowFragment
        let color = descriptor.colorAttachments[0]!
        color.isBlendingEnabled = true
        color.sourceRGBBlendFactor = .sourceAlpha
        color.destinationRGBBlendFactor = .oneMinusSourceAlpha
        color.sourceAlphaBlendFactor = .one
        color.destinationAlphaBlendFactor = .oneMinusSourceAlpha
        flow = try device.makeRenderPipelineState(descriptor: descriptor)
    }

    private static func outputFunction(_ library: any MTLLibrary, name: String,
                                       output: TurboOutput) throws -> (any MTLFunction)? {
        let constants = MTLFunctionConstantValues()
        var hdr = output == .hdr
        constants.setConstantValue(&hdr, type: .bool, index: 0)
        return try library.makeFunction(name: name, constantValues: constants)
    }

    func encode(frame: FastTurboFrame, uniforms: inout TurboUniforms,
                descriptor: MTLRenderPassDescriptor, command: any MTLCommandBuffer) throws {
        guard frame.gridSize > 1, frame.gridSize <= 16384,
              frame.buffer.length >= 3 * frame.gridSize * frame.gridSize * MemoryLayout<Float>.stride else {
            throw CanvasError.unavailable("The solver supplied an invalid field buffer. Reset the simulation to try again.")
        }
        guard frame.buffer.device.registryID == device.registryID else {
            throw CanvasError.unavailable("The solver and renderer must use the same Metal device.")
        }
        guard let encoder = command.makeRenderCommandEncoder(descriptor: descriptor) else {
            throw CanvasError.unavailable("Metal could not begin rendering the turbulence field.")
        }
        encoder.label = "FastTurbo · zero-copy field"
        encoder.setRenderPipelineState(field)
        encoder.setFragmentBuffer(frame.buffer, offset: 0, index: 0)
        encoder.setFragmentBytes(&uniforms, length: MemoryLayout<TurboUniforms>.stride, index: 1)
        encoder.drawPrimitives(type: .triangle, vertexStart: 0, vertexCount: 3)
        if uniforms.showFlowLines != 0 {
            encoder.setRenderPipelineState(flow)
            encoder.setVertexBuffer(frame.buffer, offset: 0, index: 0)
            encoder.setVertexBytes(&uniforms, length: MemoryLayout<TurboUniforms>.stride, index: 1)
            encoder.drawPrimitives(type: .line, vertexStart: 0, vertexCount: 32 * 32 * 18 * 2)
        }
        encoder.endEncoding()
    }
}

struct FastTurboCanvas: NSViewRepresentable {
    @ObservedObject var source: DisplayFrameSource
    var palette: TurboPalette
    var display: TurboField
    var exposure: Double
    var vorticitySigma: Double
    var vorticityColorRange: TurboColorRange
    var showFlowLines: Bool
    var onError: (String) -> Void
    var onPresented: (UInt64, Double) -> Void

    func makeCoordinator() -> Coordinator { Coordinator() }

    func makeNSView(context: Context) -> MTKView {
        let view = MTKView(frame: .zero, device: MTLCreateSystemDefaultDevice())
        view.colorPixelFormat = TurboOutput.hdr.pixelFormat
        view.clearColor = MTLClearColor(red: 0.012, green: 0.019, blue: 0.039, alpha: 1)
        view.colorspace = CGColorSpace(name: CGColorSpace.extendedLinearSRGB)
        (view.layer as? CAMetalLayer)?.wantsExtendedDynamicRangeContent = true
        view.framebufferOnly = true
        view.isPaused = true
        view.enableSetNeedsDisplay = true
        view.preferredFramesPerSecond = 60
        view.autoResizeDrawable = true
        view.delegate = context.coordinator
        context.coordinator.configure(view: view, onError: onError, onPresented: onPresented)
        return view
    }

    func updateNSView(_ view: MTKView, context: Context) {
        view.delegate = context.coordinator
        context.coordinator.update(self)
        if context.coordinator.needsRender { view.needsDisplay = true }
    }

    static func dismantleNSView(_ view: MTKView, coordinator: Coordinator) {
        view.delegate = nil
        coordinator.frame = nil
    }

    final class Coordinator: NSObject, MTKViewDelegate {
        private var pipeline: TurboPipeline?
        private let inFlight = DispatchSemaphore(value: 2)
        fileprivate var frame: FastTurboFrame?
        fileprivate var needsRender = true
        private var palette: TurboPalette = .ember
        private var display: TurboField = .vorticity
        private var exposure: Double = 1
        private var vorticitySigma = 2.5
        private var vorticityColorRange: TurboColorRange = .sigma
        private var showFlowLines = false
        private var onError: (String) -> Void = { _ in }
        private var lastError: String?
        private var onPresented: (UInt64, Double) -> Void = { _, _ in }
        private var generation: UInt64 = 0

        fileprivate func configure(view: MTKView, onError: @escaping (String) -> Void,
                                   onPresented: @escaping (UInt64, Double) -> Void) {
            self.onError = onError
            self.onPresented = onPresented
            guard let device = view.device else {
                report("Metal is unavailable on this Mac.")
                return
            }
            // The first window must be visible before pipeline compilation.
            DispatchQueue.global(qos: .userInitiated).async { [weak self, weak view] in
                do {
                    let pipeline = try TurboPipeline(device: device, output: .hdr)
                    DispatchQueue.main.async {
                        guard let self, let view else { return }
                        self.pipeline = pipeline
                        self.needsRender = true
                        view.needsDisplay = true
                    }
                } catch {
                    let message = error.localizedDescription
                    DispatchQueue.main.async { self?.report(message) }
                }
            }
        }

        @MainActor
        fileprivate func update(_ source: FastTurboCanvas) {
            if frame !== source.source.frame || palette != source.palette || display != source.display ||
                exposure != source.exposure || vorticitySigma != source.vorticitySigma ||
                vorticityColorRange != source.vorticityColorRange || showFlowLines != source.showFlowLines {
                needsRender = true
            }
            frame = source.source.frame
            palette = source.palette
            display = source.display
            exposure = source.exposure
            vorticitySigma = source.vorticitySigma
            vorticityColorRange = source.vorticityColorRange
            showFlowLines = source.showFlowLines
            onError = source.onError
            onPresented = source.onPresented
            generation = source.source.generation
        }

        func mtkView(_ view: MTKView, drawableSizeWillChange size: CGSize) {
            needsRender = true
            view.needsDisplay = true
        }

        func draw(in view: MTKView) {
            guard needsRender, let pipeline, view.drawableSize.width > 0, view.drawableSize.height > 0 else { return }
            guard inFlight.wait(timeout: .now()) == .success else { return }
            var submitted = false
            defer { if !submitted { inFlight.signal() } }
            guard let descriptor = view.currentRenderPassDescriptor, let drawable = view.currentDrawable,
                  let command = pipeline.queue.makeCommandBuffer() else { return }
            command.label = "FastTurbo · present"
            let retainedFrame = frame
            do {
                if let retainedFrame {
                    var uniforms = TurboUniforms(frame: retainedFrame, palette: palette, display: display,
                                                 exposure: exposure, vorticitySigma: vorticitySigma,
                                                 vorticityColorRange: vorticityColorRange,
                                                 showFlowLines: showFlowLines, size: view.drawableSize)
                    try pipeline.encode(frame: retainedFrame, uniforms: &uniforms, descriptor: descriptor, command: command)
                } else {
                    guard let encoder = command.makeRenderCommandEncoder(descriptor: descriptor) else { return }
                    encoder.endEncoding()
                }
                let didPresent = onPresented
                let frameGeneration = generation
                let hasFrame = retainedFrame != nil
                drawable.addPresentedHandler { shown in
                    let time = shown.presentedTime > 0 ? shown.presentedTime : CACurrentMediaTime()
                    if hasFrame { DispatchQueue.main.async { didPresent(frameGeneration, time) } }
                }
                command.present(drawable)
                let semaphore = inFlight
                command.addCompletedHandler { [weak self, weak view, retainedFrame] completed in
                    // Retain the frame lease until GPU sampling is finished.
                    withExtendedLifetime(retainedFrame) {}
                    semaphore.signal()
                    DispatchQueue.main.async {
                        if let error = completed.error { self?.report("Metal rendering failed: \(error.localizedDescription)") }
                        if self?.needsRender == true { view?.needsDisplay = true }
                    }
                }
                needsRender = false
                submitted = true
                command.commit()
            } catch { report(error.localizedDescription) }
        }

        private func report(_ message: String) {
            guard message != lastError else { return }
            lastError = message
            DispatchQueue.main.async { [weak self] in self?.onError(message) }
        }
    }
}

enum TurboSnapshot {
    /// A deliberately explicit, single GPU readback; interactive rendering stays zero-copy.
    static func pngData(frame: FastTurboFrame, palette: TurboPalette, display: TurboField,
                        exposure: Double, vorticitySigma: Double, vorticityColorRange: TurboColorRange,
                        showFlowLines: Bool, pixelSize: Int = 1800) throws -> Data {
        guard (64...8192).contains(pixelSize) else {
            throw CanvasError.unavailable("Snapshot size must be between 64 and 8192 pixels.")
        }
        // PNG is an 8-bit sRGB export, independent of the live HDR display.
        let pipeline = try TurboPipeline(device: frame.buffer.device, output: .sdr)
        let textureDescriptor = MTLTextureDescriptor.texture2DDescriptor(pixelFormat: TurboOutput.sdr.pixelFormat,
                                                                          width: pixelSize, height: pixelSize, mipmapped: false)
        textureDescriptor.usage = [.renderTarget]
        textureDescriptor.storageMode = .shared
        guard let texture = pipeline.device.makeTexture(descriptor: textureDescriptor),
              let command = pipeline.queue.makeCommandBuffer() else {
            throw CanvasError.unavailable("Metal could not allocate the snapshot. Try a smaller image size.")
        }
        let pass = MTLRenderPassDescriptor()
        pass.colorAttachments[0].texture = texture
        pass.colorAttachments[0].loadAction = .clear
        pass.colorAttachments[0].storeAction = .store
        pass.colorAttachments[0].clearColor = MTLClearColor(red: 0, green: 0, blue: 0, alpha: 1)
        var uniforms = TurboUniforms(frame: frame, palette: palette, display: display,
                                     exposure: exposure, vorticitySigma: vorticitySigma,
                                     vorticityColorRange: vorticityColorRange, showFlowLines: showFlowLines,
                                     size: CGSize(width: pixelSize, height: pixelSize))
        try pipeline.encode(frame: frame, uniforms: &uniforms, descriptor: pass, command: command)
        command.commit()
        command.waitUntilCompleted()
        withExtendedLifetime(frame) {}
        if let error = command.error { throw error }
        let bytesPerRow = pixelSize * 4
        var pixels = Data(count: bytesPerRow * pixelSize)
        pixels.withUnsafeMutableBytes { bytes in
            texture.getBytes(bytes.baseAddress!, bytesPerRow: bytesPerRow,
                             from: MTLRegionMake2D(0, 0, pixelSize, pixelSize), mipmapLevel: 0)
        }
        guard let provider = CGDataProvider(data: pixels as CFData),
              let colorSpace = CGColorSpace(name: CGColorSpace.sRGB),
              let image = CGImage(width: pixelSize, height: pixelSize, bitsPerComponent: 8, bitsPerPixel: 32,
                                  bytesPerRow: bytesPerRow, space: colorSpace,
                                  bitmapInfo: CGBitmapInfo(rawValue: CGImageAlphaInfo.premultipliedFirst.rawValue)
                                    .union(.byteOrder32Little),
                                  provider: provider, decode: nil, shouldInterpolate: false, intent: .defaultIntent),
              let png = NSBitmapImageRep(cgImage: image).representation(using: .png, properties: [:]) else {
            throw CanvasError.unavailable("The snapshot could not be encoded as a PNG image.")
        }
        return png
    }
}
