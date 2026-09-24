"""Check the actual source archive and relocatable native runtime staging."""
import ctypes
import importlib.util
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile

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
