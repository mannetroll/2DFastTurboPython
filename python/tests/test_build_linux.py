"""Exercise real CMake cache invalidation without compiling the GPU solver."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux build driver")
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def configure_controller(tmp_path):
    from fastturbo.build_linux import _configure

    source = tmp_path / "source"
    source.mkdir()
    # Use the production adaptation, with a real CMake compiler probe/cache.
    # No generated controller is seeded into this build directory.
    (source / "CMakeLists.txt").write_text(
        'cmake_minimum_required(VERSION 3.26)\n'
        'project(ControllerBuildRegression LANGUAGES CXX)\n'
        f'include("{ROOT / "python/native/cuda/controller.cmake"}")\n'
        'file(WRITE "${CMAKE_BINARY_DIR}/settings.txt" '
        '"${FASTTURBO_WINDOWS}\\n${CMAKE_BUILD_TYPE}\\n${CMAKE_CUDA_ARCHITECTURES}\\n${FASTTURBO_RENDERER}\\n")\n'
    )
    directory = tmp_path / "build"

    def configure(compiler, reference=ROOT / "reference-windows"):
        _configure(source, directory, CMAKE_CXX_COMPILER=compiler,
            FASTTURBO_WINDOWS=reference, CMAKE_BUILD_TYPE="Release",
            CMAKE_CUDA_ARCHITECTURES="86", FASTTURBO_RENDERER="ON")
        return directory

    return configure


def test_compiler_change_preserves_settings_and_controller_parity(tmp_path, configure_controller):
    compiler = shutil.which("c++")
    assert compiler, "The native Linux build requires a C++ compiler"
    alias = tmp_path / "different-cxx-path"
    alias.symlink_to(compiler)
    expected = json.loads((ROOT / "python/evidence/linux-native/controller-parity.json").read_text())["sha256"]
    for executable in (compiler, alias):
        directory = configure_controller(executable)
        assert (directory / "settings.txt").read_text().splitlines() == [
            str(ROOT / "reference-windows"), "Release", "86", "ON"]
        for name, digest in expected.items():
            assert hashlib.sha256((directory / "adapted" / name).read_bytes()).hexdigest() == digest


@pytest.mark.parametrize("reference", ["", "/nonexistent-fastturbo-reference"])
def test_invalid_reference_cannot_reuse_generated_controller(configure_controller, reference, capfd):
    compiler = shutil.which("c++")
    assert compiler, "The native Linux build requires a C++ compiler"
    configure_controller(compiler)
    with pytest.raises(subprocess.CalledProcessError):
        configure_controller(compiler, reference)
    errors = capfd.readouterr().err
    assert ("FASTTURBO_WINDOWS must name" if not reference else "Missing reference source:") in errors
