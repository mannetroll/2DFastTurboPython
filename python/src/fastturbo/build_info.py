"""Read the version from the VkFFT header actually supplied to the build."""
import re
from pathlib import Path


def vkfft_version(header: Path) -> str:
    match = re.search(r"VkFFTGetVersion\(\).*?return (\d+);", header.read_text(), re.S)
    if not match:
        raise RuntimeError(f"Cannot identify VkFFT version in {header}")
    value = int(match[1])
    return f"{value // 10000}.{value // 100 % 100}.{value % 100}"
