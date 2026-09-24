"""CUDA upgrades must not mix runtimes or toolkit agreements in a wheel."""
from pathlib import Path
import runpy
import sys
from types import SimpleNamespace

import pytest
import setuptools

from fastturbo.build_windows import _stage_cuda_runtime


def toolkit(root, minor):
    cuda = root / f'cuda-13.{minor}'
    (cuda / 'bin/x64').mkdir(parents=True)
    for name in ('cudart64_13.dll', 'nvrtc64_130_0.dll', f'nvrtc-builtins64_13{minor}.dll'):
        (cuda / 'bin/x64' / name).write_bytes(f'{name}:13.{minor}'.encode())
    (cuda / 'EULA.txt').write_text(f'CUDA 13.{minor} agreement')
    return cuda


def test_toolkit_upgrade_replaces_runtime_and_removes_old_builtins(tmp_path):
    native = tmp_path / 'native'
    native.mkdir()
    (native / 'fastturbo.dll').write_bytes(b'engine')
    for minor in (1, 4):
        cuda = toolkit(tmp_path, minor)
        _stage_cuda_runtime(cuda, native, f'Cuda compilation tools, release 13.{minor}, V13.{minor}.1', bundle=True)
        assert {p.name for p in native.glob('*.dll')} == {
            'fastturbo.dll', 'cudart64_13.dll', 'nvrtc64_130_0.dll', f'nvrtc-builtins64_13{minor}.dll'}
        assert (native / 'nvrtc64_130_0.dll').read_bytes().endswith(f':13.{minor}'.encode())
        assert (native / 'CUDA-EULA.txt').read_text() == f'CUDA 13.{minor} agreement'
    _stage_cuda_runtime(cuda, native, 'release 13.4', bundle=False)
    assert {p.name for p in native.glob('*.dll')} == {'fastturbo.dll'}


def test_incomplete_toolkit_cannot_destroy_working_runtime(tmp_path):
    native = tmp_path / 'native'
    native.mkdir()
    previous = toolkit(tmp_path, 1)
    _stage_cuda_runtime(previous, native, 'release 13.1', bundle=True)
    before = {p.name: p.read_bytes() for p in native.iterdir()}
    broken = toolkit(tmp_path, 4)
    (broken / 'bin/x64/nvrtc-builtins64_134.dll').unlink()
    with pytest.raises(RuntimeError, match='matching CUDA runtime'):
        _stage_cuda_runtime(broken, native, 'release 13.4', bundle=True)
    assert {p.name: p.read_bytes() for p in native.iterdir()} == before


def test_wheel_license_uses_selected_native_runtime(tmp_path, monkeypatch):
    monkeypatch.setattr(setuptools, 'setup', lambda **kwargs: None)
    namespace = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'setup.py'))
    monkeypatch.setattr(sys, 'platform', 'win32')
    source = tmp_path / 'src/fastturbo/_native/CUDA-EULA.txt'
    source.parent.mkdir(parents=True)
    source.write_bytes(b'CUDA 13.4 agreement\r\n')
    target = tmp_path / 'licenses/CUDA-EULA.txt'
    target.parent.mkdir()
    target.write_text('obsolete toolkit agreement')
    distribution = SimpleNamespace(metadata=SimpleNamespace(license_files=['LICENSE']))
    namespace['stage_native_license'](distribution, tmp_path)
    assert target.read_bytes() == source.read_bytes()
    assert distribution.metadata.license_files == ['LICENSE', 'licenses/CUDA-EULA.txt']
