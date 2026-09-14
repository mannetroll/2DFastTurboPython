"""Audit PE imports and observe loaded modules during real CUDA/VkFFT execution."""
import argparse
import ctypes as ct
from ctypes import wintypes as wt
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time

psapi = ct.WinDLL('psapi', use_last_error=True)
psapi.EnumProcessModulesEx.argtypes = [wt.HANDLE, ct.POINTER(wt.HMODULE), wt.DWORD, ct.POINTER(wt.DWORD), wt.DWORD]
psapi.EnumProcessModulesEx.restype = wt.BOOL
psapi.GetModuleFileNameExW.argtypes = [wt.HANDLE, wt.HMODULE, wt.LPWSTR, wt.DWORD]
psapi.GetModuleFileNameExW.restype = wt.DWORD

def modules(handle):
    values = (wt.HMODULE * 2048)(); needed = wt.DWORD()
    if not psapi.EnumProcessModulesEx(handle, values, ct.sizeof(values), ct.byref(needed), 3):
        return []
    paths = []
    for module in values[:needed.value // ct.sizeof(wt.HMODULE)]:
        path = ct.create_unicode_buffer(32768)
        if psapi.GetModuleFileNameExW(handle, module, path, len(path)):
            paths.append(str(Path(path.value)))
    return paths

def category(path):
    name = Path(path).name.lower(); lower = str(path).lower()
    if name.startswith(('nvrtc', 'nvptxcompiler', 'nvjitlink')): return 'VkFFT/NVRTC requirement'
    if name.startswith('cudart'): return 'CUDA runtime'
    if name.startswith('cufft'): return 'cuFFT'
    if re.match(r'(msvcp|vcruntime|concrt)\d', name): return 'MSVC runtime'
    if name.startswith(('nvcuda', 'nvapi', 'nvwgf', 'nvgpu', 'nvldum', 'nvcompiler', 'nvptxjit')) or '/filerepository/nv' in lower.replace('\\', '/'):
        return 'NVIDIA driver DLL'
    if name.startswith(('api-ms-win-', 'ext-ms-win-')) or str(Path(os.environ['SystemRoot'])).lower() in lower:
        return 'Windows system DLL'
    if name.endswith('.exe'): return 'application'
    return 'other'

def clean_environment():
    env = os.environ.copy()
    system = os.environ['SystemRoot']
    env['PATH'] = str(Path(system)/'System32') + ';' + system
    for key in list(env):
        if key.upper().startswith(('CUDA', 'NVRTC', 'VSCMD', 'VSINSTALL', 'VCINSTALL', 'VCTOOLS', 'WINDOWSSDK')) or key.upper() in ('INCLUDE', 'LIB', 'LIBPATH'):
            del env[key]
    env['CUDA_CACHE_DISABLE'] = '1'
    env['CUDA_FORCE_PRELOAD_LIBRARIES'] = '1'
    return env

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--app', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--dumpbin', type=Path, required=True)
    p.add_argument('--cwd', type=Path)
    p.add_argument('--clean', action='store_true')
    p.add_argument('--gui', action='store_true')
    a = p.parse_args(); app = a.app.resolve(); a.output.mkdir(parents=True, exist_ok=True)
    env = clean_environment() if a.clean else os.environ.copy()
    if not a.clean:
        env['PATH'] = 'C:/Program Files/NVIDIA GPU Computing Toolkit/CUDA/v13.0/bin/x64;' + env['PATH']
    imports = subprocess.run([str(a.dumpbin), '/DEPENDENTS', str(app)], capture_output=True, text=True, check=True).stdout
    (a.output/'imports.txt').write_text(imports)
    command = [str(app), '--gui-benchmark' if a.gui else '--benchmark', '--resolution', '1024', '--reynolds', '50000', '--wall-seconds', '5']
    before = sorted(f.name for f in app.parent.iterdir())
    observed = set()
    with (a.output/'stdout.log').open('w') as stdout, (a.output/'stderr.log').open('w') as stderr:
        process = subprocess.Popen(command, env=env, cwd=a.cwd or app.parent, stdout=stdout, stderr=stderr)
        while process.poll() is None:
            observed.update(modules(int(process._handle)))
            time.sleep(.02)
    after = sorted(f.name for f in app.parent.iterdir())
    rows = [{'path': path, 'category': category(path)} for path in sorted(observed, key=str.lower)]
    summary = {'app': str(app), 'sha256': hashlib.sha256(app.read_bytes()).hexdigest(),
               'size_bytes': app.stat().st_size, 'exit_code': process.returncode,
               'clean_environment': a.clean, 'PATH': env['PATH'], 'cwd': str(a.cwd or app.parent),
               'command': command, 'module_sampling_ms': 20, 'loaded_modules': rows,
               'direct_imports': re.findall(r'^\s+(\S+\.dll)\s*$', imports, re.M | re.I),
               'installation_directory_unchanged': before == after,
               'toolkit_modules': [r['path'] for r in rows if 'gpu computing toolkit' in r['path'].lower()],
               'non_system_modules': [r for r in rows if r['category'] not in ('Windows system DLL', 'NVIDIA driver DLL', 'application')]}
    if process.returncode == 0:
        summary['run'] = json.loads((a.output/'stdout.log').read_text())
    (a.output/'audit.json').write_text(json.dumps(summary, indent=2)+'\n')
    print(json.dumps({k: v for k, v in summary.items() if k not in ('run', 'loaded_modules', 'PATH')}, indent=2))
    if process.returncode != 0: raise SystemExit(f'Application failed: {process.returncode}; see {a.output}')
    if a.clean and (summary['toolkit_modules'] or summary['non_system_modules'] or not summary['installation_directory_unchanged']):
        raise SystemExit('Clean audit found an unexpected dependency or install-directory write')

if __name__ == '__main__': main()
