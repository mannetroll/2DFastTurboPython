"""Exercise a real Qt/WSLg GL context and CUDA buffer registration. No CPU fallback."""
import argparse
import ctypes as C
import ctypes.util
import json
import os
from pathlib import Path
import time

from fastturbo import get_backend_info
from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QGuiApplication, QOpenGLContext, QSurfaceFormat, QWindow


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("evidence/linux/opengl-interop.json"))
    parser.add_argument("--external-memory", action="store_true", help="Also test CUDA-exported GPU memory")
    parser.add_argument("--egl-image", action="store_true", help="Also test CUDA registration of an EGL texture image")
    args = parser.parse_args()
    report = {"hardware": get_backend_info()}
    app = QGuiApplication([])
    report["qt_platform"] = app.platformName()
    report["environment"] = {key: os.environ.get(key) for key in
                             ("DISPLAY", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR", "GALLIUM_DRIVER")}
    fmt = QSurfaceFormat()
    fmt.setVersion(3, 3)
    fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)
    window = QWindow()
    window.setSurfaceType(QWindow.SurfaceType.OpenGLSurface)
    window.setFormat(fmt)
    window.resize(400, 400)
    window.setTitle("FastTurbo CUDA / OpenGL interoperability check")
    window.show()
    # Wayland requires the compositor's configure event before the first swap.
    # A single processEvents call can run before that event has arrived.
    deadline = time.monotonic() + 5
    while not window.isExposed() and time.monotonic() < deadline:
        QCoreApplication.processEvents()
        time.sleep(.01)
    report["window_exposed"] = window.isExposed()
    if not window.isExposed():
        raise RuntimeError("The compositor did not expose the test window")
    context = QOpenGLContext()
    context.setFormat(fmt)
    report["context_created"] = context.create()
    report["context_current"] = context.makeCurrent(window)
    if not report["context_current"]:
        raise RuntimeError("Qt could not make the OpenGL context current")
    gl = C.CDLL(C.util.find_library("GL"))
    gl.glGetString.restype = C.c_char_p
    report.update({name: gl.glGetString(value).decode() for name, value in
                   (("vendor", 0x1F00), ("renderer", 0x1F01), ("version", 0x1F02))})
    gl.glGetStringi.restype = C.c_char_p
    extension_count = C.c_int()
    gl.glGetIntegerv(0x821D, C.byref(extension_count))
    extensions = {gl.glGetStringi(0x1F03, i).decode() for i in range(extension_count.value)}
    report["external_memory_extensions"] = sorted(s for s in extensions if "memory_object" in s or "semaphore" in s)
    egl = C.CDLL(C.util.find_library("EGL"))
    egl.eglGetCurrentDisplay.restype = C.c_void_p
    egl.eglQueryString.argtypes = [C.c_void_p, C.c_int]
    egl.eglQueryString.restype = C.c_char_p
    egl_display = egl.eglGetCurrentDisplay()
    if egl_display:
        egl_extensions = (egl.eglQueryString(egl_display, 0x3055) or b"").decode().split()
        report["egl_image_extensions"] = [s for s in egl_extensions if "image" in s or "dma_buf" in s]
    gl.glClearColor.argtypes = [C.c_float] * 4
    gl.glClearColor(.1, .2, .4, 1)
    gl.glClear(0x4000)
    context.swapBuffers(window)
    buffer = C.c_uint()
    gl.glGenBuffers(1, C.byref(buffer))
    gl.glBindBuffer(0x8892, buffer)
    gl.glBufferData.argtypes = [C.c_uint, C.c_ssize_t, C.c_void_p, C.c_uint]
    gl.glBufferData(0x8892, 4096, None, 0x88E8)
    gl.glFinish()
    report["gl_error"] = gl.glGetError()
    runtime = C.CDLL("libcudart.so.13")
    runtime.cudaGetErrorString.restype = C.c_char_p
    count, devices = C.c_uint(), (C.c_int * 16)()
    code = runtime.cudaGLGetDevices(C.byref(count), devices, 16, 1)
    report["cudaGLGetDevices"] = {"code": code, "error": runtime.cudaGetErrorString(code).decode(),
                                  "count": count.value}
    resource = C.c_void_p()
    code = runtime.cudaGraphicsGLRegisterBuffer(C.byref(resource), buffer, 0)
    report["cudaGraphicsGLRegisterBuffer"] = {"code": code, "error": runtime.cudaGetErrorString(code).decode()}
    report["supported"] = code == 0
    if code == 0:
        runtime.cudaGraphicsUnregisterResource(resource)
    gl.glDeleteBuffers(1, C.byref(buffer))
    if args.egl_image and egl_display:
        runtime.cudaGetLastError()
        texture = C.c_uint()
        gl.glGenTextures(1, C.byref(texture))
        gl.glBindTexture(0x0DE1, texture)
        gl.glTexImage2D(0x0DE1, 0, 0x8058, 32, 32, 0, 0x1908, 0x1401, None)
        gl.glFinish()
        egl.eglGetCurrentContext.restype = C.c_void_p
        egl.eglGetProcAddress.argtypes = [C.c_char_p]
        egl.eglGetProcAddress.restype = C.c_void_p
        create_image = C.CFUNCTYPE(C.c_void_p, C.c_void_p, C.c_void_p, C.c_uint, C.c_void_p, C.POINTER(C.c_int))(
            egl.eglGetProcAddress(b"eglCreateImageKHR"))
        destroy_image = C.CFUNCTYPE(C.c_uint, C.c_void_p, C.c_void_p)(egl.eglGetProcAddress(b"eglDestroyImageKHR"))
        attributes = (C.c_int * 3)(0x30BC, 0, 0x3038)
        egl_image = create_image(egl_display, egl.eglGetCurrentContext(), 0x30B1, texture.value, attributes)
        report["egl_image_created"] = bool(egl_image)
        if egl_image:
            runtime.cudaGraphicsEGLRegisterImage.argtypes = [C.POINTER(C.c_void_p), C.c_void_p, C.c_uint]
            code = runtime.cudaGraphicsEGLRegisterImage(C.byref(resource), egl_image, 0)
            report["cudaGraphicsEGLRegisterImage"] = {"code": code, "error": runtime.cudaGetErrorString(code).decode()}
            if code == 0:
                runtime.cudaGraphicsUnregisterResource(resource)
            report["supported"] |= code == 0
            destroy_image(egl_display, egl_image)
        gl.glDeleteTextures(1, C.byref(texture))
    if args.external_memory:
        from probe_cuda_gl_memory import probe
        # Clear the expected legacy registration error before testing a distinct API.
        runtime.cudaGetLastError()
        report["external_memory"] = probe(gl, runtime)
        report["supported"] |= report["external_memory"]["supported"]
    context.doneCurrent()
    window.close()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0 if report["supported"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
