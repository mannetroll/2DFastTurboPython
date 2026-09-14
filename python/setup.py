from pathlib import Path
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


class Build(build_py):
    def run(self):
        native_build()
        super().run()


class Editable(editable_wheel):
    def run(self):
        native_build()
        super().run()


class BinaryDistribution(Distribution):
    def has_ext_modules(self):
        return True


class Wheel(bdist_wheel):
    def get_tag(self):
        # The C ABI has no CPython version dependency.
        return "py3", "none", "win_amd64" if sys.platform == "win32" else "macosx_15_0_arm64"


class Source(sdist):
    def make_release_tree(self, base_dir, files):
        super().make_release_tree(base_dir, files)
        project = Path(__file__).resolve().parent
        reference = project.parent / "reference-macos/2DFastTurbo"
        if not reference.exists():
            reference = project / "native/reference"
        # Distribution archives must be self-contained. Reuse source paths in the
        # checkout; stage the small vendored tree only into the source archive.
        target = Path(base_dir) / "native/reference"
        for name in ("Sources", "ThirdParty", "Resources"):
            shutil.copytree(reference / name, target / name, dirs_exist_ok=True)
        windows = project.parent / "reference-windows"
        if not windows.is_dir():
            windows = project / "native/reference-windows"
        if windows.is_dir():
            destination = Path(base_dir) / "native/reference-windows"
            for name in ("src", "tools", "tests", "assets"):
                shutil.copytree(windows / name, destination / name, dirs_exist_ok=True)
            shutil.copy2(windows / "CMakeLists.txt", destination / "CMakeLists.txt")
            # Keep the Windows reference's relative VkFFT/license paths valid.
            shutil.copytree(reference / "ThirdParty", Path(base_dir) / "native/reference-macos/2DFastTurbo/ThirdParty", dirs_exist_ok=True)


setup(cmdclass={"build_py": Build, "editable_wheel": Editable, "bdist_wheel": Wheel, "sdist": Source},
      distclass=BinaryDistribution)
