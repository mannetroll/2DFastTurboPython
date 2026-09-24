"""Check the actual source archive and relocatable native runtime staging."""
import ctypes
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import zipfile

import pytest

PROJECT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('check_distributions', PROJECT / 'scripts/check_distributions.py')
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


def sdist(project, destination):
    subprocess.run([sys.executable, 'setup.py', 'sdist', '--dist-dir', str(destination)],
                   cwd=project, check=True, capture_output=True, text=True)
    archive, = destination.glob('*.tar.gz')
    return archive


def test_sdist_can_be_rebuilt_without_repository(tmp_path):
    first = sdist(PROJECT, tmp_path / 'first')
    assert checker.check(first)[1] == 'sdist'
    with tarfile.open(first) as archive:
        archive.extractall(tmp_path / 'isolated', filter='data')
    extracted, = (tmp_path / 'isolated').iterdir()
    second = sdist(extracted, tmp_path / 'second')
    assert checker.check(first) == checker.check(second)
    with tarfile.open(first) as a, tarfile.open(second) as b:
        # The source-only round trip preserves native code, not just filenames.
        for name in checker.SOURCE_FILES:
            assert a.extractfile(f'{extracted.name}/{name}').read() == b.extractfile(f'{extracted.name}/{name}').read()


@pytest.mark.skipif(sys.platform != 'linux', reason='ELF runtime staging')
def test_bundled_runtime_is_relocatable(tmp_path):
    from fastturbo.build_linux import _bundle_cuda

    if not shutil.which('cc') or not shutil.which('patchelf'):
        pytest.skip('C compiler and patchelf are required')
    toolkit = tmp_path / 'toolkit'
    toolkit.mkdir()
    (toolkit / 'EULA.txt').write_text('test toolkit notice')
    source = toolkit / 'runtime.c'
    source.write_text('int runtime_value(void) { return 13; }\n')
    names = ('libcudart.so.13', 'libnvrtc.so.13', 'libnvrtc-builtins.so.13.1')
    for name in names:
        subprocess.run(['cc', '-shared', '-fPIC', f'-Wl,-soname,{name}', str(source),
                        '-o', str(toolkit / name)], check=True)
    build = tmp_path / 'build'
    build.mkdir()
    (build / 'cuda-libraries.txt').write_text('\n'.join(str(toolkit / n) for n in names[:2]) + '\n')
    native = tmp_path / 'native'
    native.mkdir()
    _bundle_cuda(build, native, toolkit)
    relocated = tmp_path / 'relocated'
    native.rename(relocated)
    shutil.rmtree(toolkit)
    for name in names:
        assert ctypes.CDLL(str(relocated / name)).runtime_value() == 13
        assert subprocess.check_output(['patchelf', '--print-rpath', str(relocated / name)], text=True).strip() == '$ORIGIN'
    assert (relocated / 'CUDA-EULA.txt').read_text() == 'test toolkit notice'


def test_missing_reference_fails_source_archive(tmp_path):
    first = sdist(PROJECT, tmp_path / 'first')
    with tarfile.open(first) as archive:
        archive.extractall(tmp_path / 'isolated', filter='data')
    extracted, = (tmp_path / 'isolated').iterdir()
    shutil.rmtree(extracted / 'native/reference-windows')
    with pytest.raises(subprocess.CalledProcessError) as failure:
        sdist(extracted, tmp_path / 'second')
    assert 'reference-windows sources are missing' in failure.value.stderr


@pytest.mark.parametrize('variant', ['matching', 'crlf', 'stale', 'missing', 'extra'])
def test_release_rejects_mixed_sources_at_the_same_version(tmp_path, variant):
    source = tmp_path / 'fastturbo-0.1.2.tar.gz'
    module = 'fastturbo/engine.py'
    code = b'class Configuration:\n    start_spectrum = "PAO"\n'
    with tarfile.open(source, 'w:gz') as archive:
        member = tarfile.TarInfo('fastturbo-0.1.2/src/' + module)
        member.size = len(code)
        archive.addfile(member, io.BytesIO(code))
    wheel = tmp_path / 'fastturbo-0.1.2-py3-none-win_amd64.whl'
    with zipfile.ZipFile(wheel, 'w') as archive:
        if variant != 'missing':
            content = b'class Configuration: pass\n' if variant == 'stale' else code
            if variant == 'crlf':
                content = content.replace(b'\n', b'\r\n')
            archive.writestr(module, content)
        if variant == 'extra':
            archive.writestr('fastturbo/obsolete.py', b'pass\n')
    if variant in {'matching', 'crlf'}:
        checker.check_release_sources([source, wheel])
    else:
        with pytest.raises(AssertionError, match='Wheels do not match the sdist'):
            checker.check_release_sources([source, wheel])


@pytest.mark.parametrize('variant', ['matching', 'stale-dll', 'wrong-builtins', 'wrong-license'])
def test_windows_runtime_release_checks(tmp_path, variant):
    wheel = tmp_path / 'windows.whl'
    distinfo = 'fastturbo-0.1.2.dist-info'
    with zipfile.ZipFile(wheel, 'w') as archive:
        manifest = {'nvcc': 'Cuda compilation tools, release 13.4, V13.4.92',
                    'distribution': 'OFF', 'architectures': '86-real;86-virtual'}
        archive.writestr('fastturbo/_native/build-manifest.json', json.dumps(manifest))
        for name in ('cudart64_13.dll', 'nvrtc64_130_0.dll',
                     'nvrtc-builtins64_131.dll' if variant == 'wrong-builtins' else 'nvrtc-builtins64_134.dll'):
            archive.writestr('fastturbo/_native/' + name, b'runtime')
        if variant == 'stale-dll':
            archive.writestr('fastturbo/_native/nvrtc-builtins64_131.dll', b'old runtime')
        archive.writestr('fastturbo/_native/CUDA-EULA.txt', b'current agreement\r\n')
        archive.writestr(distinfo + '/licenses/licenses/CUDA-EULA.txt',
                         b'old agreement' if variant == 'wrong-license' else b'current agreement\n')
    with zipfile.ZipFile(wheel) as archive:
        if variant == 'matching':
            checker.check_windows_runtime(archive, distinfo)
        else:
            with pytest.raises(AssertionError):
                checker.check_windows_runtime(archive, distinfo)


@pytest.mark.parametrize('variant', ['raw', 'crlf', 'mixed', 'changed'])
def test_native_source_manifest_comparison(tmp_path, variant):
    source = tmp_path / 'fastturbo-0.1.2.tar.gz'
    code = b'// native solver\nvoid step() {}\n'
    with tarfile.open(source, 'w:gz') as archive:
        member = tarfile.TarInfo('fastturbo-0.1.2/native/reference-windows/src/solver.cu')
        member.size = len(code)
        archive.addfile(member, io.BytesIO(code))
    built = code.replace(b'\n', b'\r\n') if variant == 'crlf' else code
    if variant == 'mixed':
        built = code.replace(b'\n', b'\r\n', 1)
    if variant == 'changed':
        built = code.replace(b'step()', b'other()')
    path = r'C:\Users\builder\2DFastTurboPython\reference-windows\src\solver.cu'
    manifest = {'files': {path: hashlib.sha256(built).hexdigest()}}
    if variant in ('mixed', 'changed'):
        manifest['files_lf'] = {path: hashlib.sha256(built.replace(b'\r\n', b'\n')).hexdigest()}
    wheel = tmp_path / 'windows.whl'
    with zipfile.ZipFile(wheel, 'w') as archive:
        archive.writestr('fastturbo/_native/build-manifest.json', json.dumps(manifest))
    if variant == 'changed':
        with pytest.raises(AssertionError, match='native source differs'):
            checker.check_native_sources([source, wheel])
    else:
        checker.check_native_sources([source, wheel])
