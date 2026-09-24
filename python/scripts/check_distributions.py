"""Reject incomplete source archives and non-release wheels before upload."""
from __future__ import annotations

import argparse
from email.parser import BytesParser
import json
from pathlib import Path
import re
import subprocess
import tarfile
import tempfile
import zipfile

SOURCE_FILES = {
    'pyproject.toml', 'setup.py', 'LICENSE', 'MANIFEST.in',
    'src/fastturbo/build_linux.py', 'native/fastturbo.h',
    'native/linux/CMakeLists.txt', 'native/linux/cuda_driver.cpp',
    'native/cuda/core.cmake', 'native/cuda/controller.cmake',
    'native/cuda/legacy_cnab2.cu', 'native/windows/vendor/json.hpp',
    'native/reference-windows/src/solver.cu', 'native/reference-windows/src/fft.cpp',
    'native/reference-windows/src/controller.cpp', 'native/reference-windows/src/main.cpp',
    'native/reference-windows/tests/core_tests.cpp',
    'native/reference-macos/2DFastTurbo/ThirdParty/VkFFTSource/vkFFT/vkFFT.h',
    'native/reference/Sources/FastTurboApp/TurboRendering.metal',
    'native/PortBridge.swift', 'packaging/manylinux.Dockerfile',
}


def check(path: Path) -> tuple[str, str]:
    if path.name.endswith('.tar.gz'):
        with tarfile.open(path) as archive:
            members = archive.getmembers()
            roots = {m.name.split('/')[0] for m in members}
            assert len(roots) == 1, 'sdist must have one top-level directory'
            root = roots.pop()
            names = {m.name.removeprefix(root + '/') for m in members}
            assert SOURCE_FILES <= names, f'Missing source files: {SOURCE_FILES - names}'
            for member in members:
                assert not member.issym() and not member.islnk(), member.name
                assert not member.name.startswith('/') and '..' not in Path(member.name).parts
                assert '/_native/' not in member.name, member.name
                assert not re.search(r'\.(so(?:\..*)?|dll|dylib|o|pyc|whl)$', member.name), member.name
            metadata = BytesParser().parsebytes(archive.extractfile(f'{root}/PKG-INFO').read())
            assert metadata['License-Expression'] == 'MIT AND Apache-2.0'
            version = metadata['Version']
            assert root == f'fastturbo-{version}' and path.name == f'{root}.tar.gz'
        return version, 'sdist'

    assert path.suffix == '.whl', f'Unexpected release artifact: {path}'
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        metadata_path, = [n for n in names if n.endswith('.dist-info/METADATA')]
        distinfo = metadata_path.rsplit('/', 1)[0]
        metadata = BytesParser().parsebytes(archive.read(metadata_path))
        version = metadata['Version']
        wheel = BytesParser().parsebytes(archive.read(f'{distinfo}/WHEEL'))
        assert wheel['Root-Is-Purelib'] == 'false'
        tags = wheel.get_all('Tag')
        assert tags and all(t.startswith('py3-none-') for t in tags), tags
        platforms = {t.removeprefix('py3-none-') for t in tags}
        assert path.name == f'fastturbo-{version}-py3-none-' + '.'.join(sorted(platforms)) + '.whl'
        for name in ('bench.py', 'build.py', 'assets/fastturbo.png', '_native/build-manifest.json'):
            assert f'fastturbo/{name}' in names, name
        entrypoints = archive.read(f'{distinfo}/entry_points.txt').decode()
        assert '2dbench =' in entrypoints and '2dfastturbo =' in entrypoints
        assert metadata['Requires-Python'] == '>=3.13'
        assert any(n.endswith('/licenses/LICENSE') for n in names)
        if platforms == {'macosx_15_0_arm64'}:
            assert 'fastturbo/_native/libfastturbo.dylib' in names
            assert 'fastturbo/_native/default.metallib' in names
            return version, 'macos'
        if platforms == {'win_amd64'}:
            for prefix in ('fastturbo.dll', 'cudart64_', 'nvrtc64_', 'nvrtc-builtins64_'):
                assert any(n.startswith(f'fastturbo/_native/{prefix}') for n in names), prefix
            return version, 'windows'
        assert platforms == {'manylinux_2_28_x86_64'}, f'Unselected Linux wheel platform: {platforms}'
        assert 'LicenseRef-NVIDIA-CUDA' in metadata['License-Expression']
        for prefix in ('libfastturbo.so', 'libfastturbo_gl.so', 'libcudart.so.', 'libnvrtc.so.', 'libnvrtc-builtins.so.', 'CUDA-EULA.txt', 'Linux-Libraries-LICENSE.txt'):
            assert any(n.startswith(f'fastturbo/_native/{prefix}') for n in names), prefix
        assert not any(Path(n).name.startswith(('libcuda.so', 'libcuda-', 'libnvidia-')) for n in names)
        assert not any(Path(n).name.startswith(('libGLX', 'libOpenGL', 'libGLdispatch', 'libGL.so', 'libGL-'))
                       for n in names), 'OpenGL loaders must come from the host driver, not the wheel'
        manifest = json.loads(archive.read('fastturbo/_native/build-manifest.json'))
        assert manifest['bundle_cuda'] == 'ON' and manifest['renderer'] == 'ON'
        assert manifest['architectures'] != 'native' and '-virtual' in manifest['architectures']
        with tempfile.TemporaryDirectory() as directory:
            for name in names:
                if '.so' not in Path(name).name:
                    continue
                library = Path(directory) / Path(name).name
                library.write_bytes(archive.read(name))
                dynamic = subprocess.check_output(['readelf', '-d', str(library)], text=True)
                assert 'Shared library: [libcuda.so' not in dynamic, name
                if name == 'fastturbo/_native/libfastturbo_gl.so':
                    assert 'Shared library: [libGL.so.1]' in dynamic, 'Renderer must link the system libGL ABI'
                for line in dynamic.splitlines():
                    if '(RPATH)' in line or '(RUNPATH)' in line:
                        assert all(p.startswith('$ORIGIN') for p in line.split('[')[1].split(']')[0].split(':')), line
        return version, 'linux'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release-set', action='store_true', help='Require macOS, Windows, Linux and sdist at one version')
    parser.add_argument('artifacts', nargs='+', type=Path)
    args = parser.parse_args()
    checked = [check(path) for path in args.artifacts]
    assert len({v for v, _ in checked}) == 1, 'Mixed release versions'
    if args.release_set:
        assert sorted(kind for _, kind in checked) == ['linux', 'macos', 'sdist', 'windows'], checked
    for path in args.artifacts:
        print(f'OK {path.name}')


if __name__ == '__main__':
    main()
