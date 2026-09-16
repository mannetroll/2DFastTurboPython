"""Exercise a real Qt/WSLg GL context and CUDA buffer registration. No CPU fallback."""
import argparse
import ctypes as C
import ctypes.util
import json
from pathlib import Path

from fastturbo import get_backend_info
from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QGuiApplication, QOpenGLContext, QSurfaceFormat, QWindow


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("evidence/linux/opengl-interop.json"))
    args = parser.parse_args()
    report = {"hardware": get_backend_info()}
    app = QGuiApplication([])
    fmt = QSurfaceFormat()
    fmt.setVersion(3, 3)
    fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)
    window = QWindow()
    window.setSurfaceType(QWindow.SurfaceType.OpenGLSurface)
    window.setFormat(fmt)
    window.resize(400, 400)
    window.setTitle("FastTurbo CUDA / OpenGL interoperability check")
    window.show()
    QCoreApplication.processEvents()
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
    context.doneCurrent()
    window.close()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0 if report["supported"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
