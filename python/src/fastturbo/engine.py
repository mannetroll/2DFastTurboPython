"""Opaque ctypes handles. Only small JSON telemetry crosses the normal boundary.

Field/spectrum exports are explicit file operations for validation; rendering and
normal integration never map GPU fields into Python.
"""
from __future__ import annotations
import ctypes as C
from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
import threading

from .build import NATIVE, build


@dataclass(frozen=True)
class Configuration:
    resolution: int = 1024
    reynolds: float = 27898.36614974335
    k0: int = 4
    seed: int = 2
    cfl: float = 3.0
    integrator: str = "etdrk4"
    operator: str = "stress"
    fixed_dt: float | None = None
    start_spectrum: str = "PAO"

    @property
    def padded_resolution(self):
        return 3 * self.resolution // 2

    @staticmethod
    def calibrated_reynolds(resolution: int, k0: int) -> float:
        return 10 ** (1.066240 * math.log10(resolution) - 0.083911 * math.log10(k0) + 1.286396)

    def payload(self):
        if self.start_spectrum not in ("PAO", "KM3"):
            raise ValueError("Start spectrum must be PAO or KM3")
        if isinstance(self.seed, bool) or not isinstance(self.seed, int) or not 0 <= self.seed < 2**64:
            raise ValueError("Seed must be an unsigned 64-bit integer")
        value = asdict(self)
        value["seed"] = str(self.seed)
        return value


class NativeError(RuntimeError):
    pass


class _Library:
    def __init__(self):
        if threading.current_thread() is not threading.main_thread():
            raise NativeError("Initialize the native library on the main thread before using workers")
        library = build()
        # Keep the DLL directory alive, including NVRTC's delayed builtins load.
        import os
        self._dll_directory = os.add_dll_directory(str(library.parent)) if os.name == "nt" else None
        # NVRTC uses a legacy delayed LoadLibrary for its builtins, which does
        # not honor AddDllDirectory in an ordinary Python process. Preload the
        # packaged module by absolute path and retain it for the library lifetime.
        self._runtime_libraries = [C.CDLL(str(p)) for p in library.parent.glob("nvrtc-builtins*.dll")] if os.name == "nt" else []
        self.lib = C.CDLL(str(library))  # CDLL releases the GIL during native calls.
        specs = {
            "ft_string_free": ([C.c_void_p], None),
            "ft_initialize": ([C.c_char_p], C.c_void_p),
            "ft_engine_create": ([C.c_char_p, C.POINTER(C.c_void_p)], C.c_void_p),
            "ft_engine_destroy": ([C.c_void_p], None),
            "ft_engine_call": ([C.c_void_p, C.c_char_p], C.c_void_p),
            "ft_ui_create": ([], C.c_void_p),
            "ft_ui_destroy": ([C.c_void_p], None),
            "ft_ui_attach": ([C.c_void_p, C.c_void_p], C.c_void_p),
            "ft_ui_call": ([C.c_void_p, C.c_char_p], C.c_void_p),
        }
        for name, (args, result) in specs.items():
            function = getattr(self.lib, name)
            function.argtypes, function.restype = args, result
        self.info = self.result(self.lib.ft_initialize(str(NATIVE).encode()))
        if self.info["abi_version"] != 1:
            raise NativeError("Incompatible FastTurbo ABI version")

    def string(self, pointer):
        if not pointer:
            raise NativeError("Native call returned no response")
        try:
            return C.string_at(pointer).decode()
        finally:
            self.lib.ft_string_free(pointer)

    def result(self, pointer):
        result = json.loads(self.string(pointer))
        if "error" in result:
            raise NativeError(result["error"])
        return result


_library: _Library | None = None


def native_library():
    global _library
    if _library is None:
        _library = _Library()
    return _library


def get_backend_info():
    """Return backend identity, GPU, defaults and available capabilities."""
    from copy import deepcopy
    info = deepcopy(native_library().info)
    info.setdefault("backend", "METAL / VkFFT")
    info.setdefault("capabilities", {"native_rendering": True, "forcing": False,
                                     "drag": False, "vortex_injection": False})
    return info


def encode(value):
    return json.dumps(value, allow_nan=False, separators=(",", ":")).encode()


class Engine:
    """Headless, synchronous batch API with an independently serialized native core.

    Use a context manager or close(). A Python lock excludes calls racing close;
    the native backend owns all GPU state. No Python callbacks.
    """
    def __init__(self, config: Configuration | None = None, *, initial: str | Path | None = None):
        self._native = native_library()
        self._lock = threading.RLock()
        self._handle = None
        self.config = config or Configuration()
        payload = self.config.payload()
        if initial is not None:
            payload["initial"] = str(initial)
        error = C.c_void_p()
        self._handle = self._native.lib.ft_engine_create(encode(payload), C.byref(error))
        if not self._handle:
            raise NativeError(self._native.string(error.value))

    def _call(self, op, **kwargs):
        with self._lock:
            if not self._handle:
                raise NativeError("Engine is closed")
            return self._native.result(self._native.lib.ft_engine_call(self._handle, encode({"op": op, **kwargs})))

    def advance(self, steps: int = 1, *, until: float | None = None):
        if isinstance(steps, bool) or not isinstance(steps, int) or steps < 0:
            raise ValueError("Steps must be a nonnegative integer")
        return self._call("advance", steps=steps, until=until)

    def inspect(self):
        return self._call("inspect")

    def set_parameters(self, *, reynolds=None, cfl=None):
        return self._call("set", **{k: v for k, v in {"reynolds": reynolds, "cfl": cfl}.items() if v is not None})

    def reset(self, *, initial=None):
        return self._call("reset", initial=str(initial) if initial is not None else None)

    def export(self, *, spectrum=None, fields=None, png=None, snapshot=False, **render_options):
        options = {k: str(v) for k, v in {"spectrum": spectrum, "fields": fields, "png": png}.items() if v is not None}
        return self._call("export", **options, snapshot=snapshot, **render_options)

    def close(self):
        with self._lock:
            if self._handle:
                self._native.lib.ft_engine_destroy(self._handle)
                self._handle = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def __del__(self):
        if getattr(self, "_handle", None):
            self.close()


class NativeUI:
    """Main-thread control plane over the reference native controller/pump."""
    def __init__(self):
        self._native = native_library()
        if not self._native.info.get("capabilities", {}).get("native_rendering", True):
            raise NativeError(self._native.info.get("native_rendering_error", "Native GPU rendering is unavailable in this build."))
        self._handle = self._native.lib.ft_ui_create()
        if not self._handle:
            raise NativeError("Could not create native UI controller")

    @property
    def backend_info(self):
        return get_backend_info()

    def call(self, op="get", **kwargs):
        if threading.current_thread() is not threading.main_thread():
            raise NativeError("UI operations require the main thread")
        if not self._handle:
            raise NativeError("UI is closed")
        return self._native.result(self._native.lib.ft_ui_call(self._handle, encode({"op": op, **kwargs})))

    def attach(self, native_window: int):
        if threading.current_thread() is not threading.main_thread() or not self._handle:
            raise NativeError("Attach requires a live UI on the main thread")
        return self._native.result(self._native.lib.ft_ui_attach(self._handle, native_window))

    def close(self):
        if self._handle:
            if threading.current_thread() is not threading.main_thread():
                raise NativeError("Close UI on the main thread")
            self._native.lib.ft_ui_destroy(self._handle)
            self._handle = None
