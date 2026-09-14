from pathlib import Path
import runpy
import shutil
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
        # ctypes has no CPython ABI dependency; the dylib requires macOS 15 arm64.
        return "py3", "none", "macosx_15_0_arm64"


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


setup(cmdclass={"build_py": Build, "editable_wheel": Editable, "bdist_wheel": Wheel, "sdist": Source},
      distclass=BinaryDistribution)
