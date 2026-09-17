"""Nsight capture of warmed native batches, diagnostics and GPU snapshots only."""
import ctypes as C
from fastturbo import Configuration, Engine


with Engine(Configuration(resolution=1024, reynolds=50000)) as engine:
    engine.advance(16)
    engine.export(snapshot=True)
    engine.reset()
    runtime = C.CDLL("libcudart.so.13")
    if runtime.cudaProfilerStart() != 0:
        raise RuntimeError("Cannot start CUDA profiler")
    try:
        for _ in range(64):
            engine.advance(8)
        engine.inspect()
        engine.export(snapshot=True, include_velocity=False)
        engine.export(snapshot=True, include_velocity=True)
    finally:
        runtime.cudaProfilerStop()
