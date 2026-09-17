"""Compact runtime information for the desktop header, queried once at launch."""
from __future__ import annotations

import platform


def _cuda_version(value: int) -> str:
    return f"{value // 1000}.{value % 1000 // 10}"


def environment_summary(info: dict) -> tuple[str, str]:
    """Return a single-line subtitle and the complete hover description."""
    gpu = info.get("gpu", "GPU")
    short_gpu = gpu.removeprefix("NVIDIA GeForce ")
    parts = [short_gpu]
    details = [f"GPU: {gpu}"]

    working_set = info.get("recommended_working_set_bytes", 0)
    gpu_memory = info.get("gpu_memory_bytes", 0)
    if working_set:
        size = f"{working_set / 1024**3:.1f} GiB"
        parts.append(size)
        details.append(f"Metal recommended working set: {size}")
    elif gpu_memory:
        size = f"{gpu_memory / 1024**3:.1f} GiB"
        parts.append(size)
        details.append(f"GPU memory: {size}")

    if info.get("cuda_runtime_version"):
        version = _cuda_version(info["cuda_runtime_version"])
        parts.append(f"CUDA {version}")
        details.append(f"CUDA runtime: {version}")
    if info.get("cuda_driver_version"):
        details.append(f"CUDA driver: {_cuda_version(info['cuda_driver_version'])}")

    python = platform.python_version()
    parts.append(f"Py {python}")
    system = platform.system()
    if system == "Darwin":
        parts.append(f"macOS {platform.mac_ver()[0]}")
    elif system == "Windows":
        parts.append(f"Win {platform.release()}")
    elif system == "Linux":
        try:
            distribution = platform.freedesktop_os_release()
        except OSError:
            distribution = {}
        name = distribution.get("NAME") or "Linux"
        version = distribution.get("VERSION_ID", "")
        parts.append(f"{name} {version}".strip())
        if distribution:
            details.append(f"Distribution: {distribution.get('PRETTY_NAME') or parts[-1]}")
    else:
        parts.append(f"{system} {platform.release()}")
    details += [f"Python: {python} ({platform.python_implementation()})",
                f"OS: {platform.platform()}"]
    return " · ".join(parts), "\n".join(details)
