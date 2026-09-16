"""Diagnostic only: test CUDA-exported device memory with GL_EXT_memory_object_fd."""
import ctypes as C
import os
import sys


class Location(C.Structure):
    _fields_ = [("type", C.c_int), ("id", C.c_int)]


class Properties(C.Structure):
    _fields_ = [("type", C.c_int), ("handles", C.c_int), ("location", Location),
                ("metadata", C.c_void_p), ("flags", C.c_ubyte * 8)]


class Access(C.Structure):
    _fields_ = [("location", Location), ("flags", C.c_ulonglong)]


def probe(gl, runtime):
    driver = C.CDLL("libcuda.so.1")
    driver.cuGetErrorString.argtypes = [C.c_int, C.POINTER(C.c_char_p)]
    result = {}
    def check(name, *args):
        code = getattr(driver, name)(*args)
        error = C.c_char_p()
        driver.cuGetErrorString(code, C.byref(error))
        result[name] = {"code": code, "error": error.value.decode() if error.value else "unknown"}
        print(name, result[name], file=sys.stderr, flush=True)
        if code:
            raise RuntimeError(f"{name}: {result[name]}")
    gl.glXGetProcAddressARB.restype = C.c_void_p
    gl.glXGetProcAddressARB.argtypes = [C.c_char_p]
    def function(name, *args):
        address = gl.glXGetProcAddressARB(name.encode())
        if not address:
            raise RuntimeError(f"Missing {name}")
        return C.CFUNCTYPE(None, *args)(address)
    handle, address = C.c_ulonglong(), C.c_ulonglong()
    memory, buffer = C.c_uint(), C.c_uint()
    fd = C.c_int(-1)
    size = C.c_size_t()
    mapped = False
    try:
        prop = Properties(type=1, handles=1, location=Location(1, 0))
        check("cuMemGetAllocationGranularity", C.byref(size), C.byref(prop), 0)
        check("cuMemCreate", C.byref(handle), size, C.byref(prop), C.c_ulonglong(0))
        check("cuMemAddressReserve", C.byref(address), size, C.c_size_t(0), C.c_ulonglong(0), C.c_ulonglong(0))
        check("cuMemMap", address, size, C.c_size_t(0), handle, C.c_ulonglong(0))
        mapped = True
        access = Access(Location(1, 0), 3)
        check("cuMemSetAccess", address, size, C.byref(access), C.c_size_t(1))
        check("cuMemsetD32_v2", address, C.c_uint(42), C.c_size_t(size.value // 4))
        code = runtime.cudaDeviceSynchronize()
        if code:
            raise RuntimeError(f"CUDA synchronization: {code}")
        check("cuMemExportToShareableHandle", C.byref(fd), handle, 1, C.c_ulonglong(0))
        result["allocation_bytes"] = size.value
        result["exported_fd_target"] = os.readlink(f"/proc/self/fd/{fd.value}")
        print("CUDA allocation exported:", result["exported_fd_target"], file=sys.stderr, flush=True)
        function("glCreateMemoryObjectsEXT", C.c_int, C.POINTER(C.c_uint))(1, C.byref(memory))
        dedicated = C.c_int(int(os.environ.get("FASTTURBO_PROBE_DEDICATED", "0")))
        function("glMemoryObjectParameterivEXT", C.c_uint, C.c_uint, C.POINTER(C.c_int))(memory, 0x9581, C.byref(dedicated))
        print("GL dedicated allocation:", dedicated.value, "GL error:", gl.glGetError(), file=sys.stderr, flush=True)
        print("Importing GPU memory into OpenGL", file=sys.stderr, flush=True)
        function("glImportMemoryFdEXT", C.c_uint, C.c_ulonglong, C.c_uint, C.c_int)(memory, size.value, 0x9586, fd)
        error = gl.glGetError()
        result["glImportMemoryFdEXT_error"] = error
        if error:
            raise RuntimeError(f"OpenGL rejected CUDA allocation: {hex(error)}")
        fd.value = -1  # GL owns the successfully imported descriptor.
        gl.glGenBuffers(1, C.byref(buffer))
        gl.glBindBuffer(0x8892, buffer)
        function("glBufferStorageMemEXT", C.c_uint, C.c_ssize_t, C.c_uint, C.c_ulonglong)(0x8892, size.value, memory, 0)
        error = gl.glGetError()
        result["glBufferStorageMemEXT_error"] = error
        if error:
            raise RuntimeError(f"OpenGL storage failed: {hex(error)}")
        # Four bytes only, for this explicit diagnostic, to verify GPU identity.
        value = C.c_uint()
        gl.glGetBufferSubData(0x8892, C.c_ssize_t(0), C.c_ssize_t(4), C.byref(value))
        result["readback"] = value.value
        result["supported"] = value.value == 42 and gl.glGetError() == 0
    except RuntimeError as error:
        result["supported"] = False
        result["error"] = str(error)
    finally:
        if buffer.value:
            gl.glDeleteBuffers(1, C.byref(buffer))
        if memory.value:
            function("glDeleteMemoryObjectsEXT", C.c_int, C.POINTER(C.c_uint))(1, C.byref(memory))
        if fd.value >= 0:
            os.close(fd.value)
        if mapped:
            driver.cuMemUnmap(address, size)
        if address.value:
            driver.cuMemAddressFree(address, size)
        if handle.value:
            driver.cuMemRelease(handle)
    return result
