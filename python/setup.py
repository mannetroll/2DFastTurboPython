from pathlib import Path
import os
import runpy
import shutil
import sys
from setuptools import Distribution, setup
from setuptools.command.build_py import build_py
from setuptools.command.editable_wheel import editable_wheel
from setuptools.command.sdist import sdist
from setuptools.command.bdist_wheel import bdist_wheel


def native_build():
    runpy.run_path(str(Path(__file__).parent / "src/fastturbo/build.py"))["build"]()


def stage_native_license(distribution, project):
    """Use the bundled runtime's own agreement in dist-info as well as _native."""
    source = project / "src/fastturbo/_native/CUDA-EULA.txt"
    if sys.platform in ("win32", "linux") and source.is_file():
        destination = project / "licenses/CUDA-EULA.txt"
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        distribution.metadata.license_files = sorted(set(
            distribution.metadata.license_files or ()) | {"licenses/CUDA-EULA.txt"})


class Build(build_py):
    def run(self):
        native_build()
        stage_native_license(self.distribution, Path(__file__).parent)
        # setuptools otherwise retains files from an earlier bundled build.
        staged = Path(self.build_lib) / "fastturbo/_native"
        if staged.is_dir():
            shutil.rmtree(staged)
        super().run()


class Editable(editable_wheel):
    def run(self):
        native_build()
        stage_native_license(self.distribution, Path(__file__).parent)
        super().run()


class BinaryDistribution(Distribution):
    def has_ext_modules(self):
        return True


class Wheel(bdist_wheel):
    def get_tag(self):
        # The C ABI has no CPython version dependency.
        if sys.platform == "linux":
            return "py3", "none", super().get_tag()[2]
        return "py3", "none", "win_amd64" if sys.platform == "win32" else "macosx_15_0_arm64"


class Source(sdist):
    def run(self):
        # Source archives contain Metal headers but no CUDA runtime binaries.
        self.distribution.metadata.license_expression = "MIT AND Apache-2.0"
        super().run()

    def make_release_tree(self, base_dir, files):
        super().make_release_tree(base_dir, files)
        project = Path(__file__).resolve().parent
        reference = project.parent / "reference-macos/2DFastTurbo"
        if not reference.exists():
            reference = project / "native/reference"
        # Distribution archives must be self-contained. Reuse source paths in the
        # checkout; stage the small vendored tree only into the source archive.
        target = Path(base_dir) / "native/reference"
        ignore = shutil.ignore_patterns("__pycache__", "*.pyc", "*.so", "*.so.*", "*.dll", "*.dylib", "*.o", ".DS_Store")
        for name in ("Sources", "ThirdParty", "Resources"):
            shutil.copytree(reference / name, target / name, dirs_exist_ok=True, ignore=ignore)
        windows = project.parent / "reference-windows"
        if not windows.is_dir():
            windows = project / "native/reference-windows"
        if not windows.is_dir():
            raise RuntimeError("Cannot build a complete sdist: reference-windows sources are missing.")
        destination = Path(base_dir) / "native/reference-windows"
        for name in ("src", "tools", "tests", "assets"):
            shutil.copytree(windows / name, destination / name, dirs_exist_ok=True, ignore=ignore)
        shutil.copy2(windows / "CMakeLists.txt", destination / "CMakeLists.txt")
        # Keep the Windows reference's relative VkFFT/license paths valid.
        shutil.copytree(reference / "ThirdParty", Path(base_dir) / "native/reference-macos/2DFastTurbo/ThirdParty", dirs_exist_ok=True, ignore=ignore)


def license_expression():
    if sys.platform == "win32":
        return "MIT AND LicenseRef-NVIDIA-CUDA"
    if sys.platform == "linux":
        if os.environ.get("FASTTURBO_BUNDLE_CUDA", "OFF").upper() == "ON":
            return "MIT AND LicenseRef-NVIDIA-CUDA AND LicenseRef-Linux-Libraries"
        return "MIT"
    return "MIT AND Apache-2.0"


setup(cmdclass={"build_py": Build, "editable_wheel": Editable, "bdist_wheel": Wheel, "sdist": Source},
      distclass=BinaryDistribution,
      license_expression=license_expression())
