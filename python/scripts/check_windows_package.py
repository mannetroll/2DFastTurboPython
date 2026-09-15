"""Run the frozen application with Windows-only PATH and audit loaded modules."""
from __future__ import annotations
import argparse
import ctypes as C
from ctypes import wintypes as W
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time


def modules(pid):
    kernel = C.WinDLL("kernel32", use_last_error=True)
    psapi = C.WinDLL("psapi", use_last_error=True)
    kernel.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
    kernel.OpenProcess.restype = W.HANDLE
    kernel.CloseHandle.argtypes = [W.HANDLE]
    psapi.EnumProcessModulesEx.argtypes = [W.HANDLE, C.POINTER(W.HMODULE), W.DWORD, C.POINTER(W.DWORD), W.DWORD]
    psapi.GetModuleFileNameExW.argtypes = [W.HANDLE, W.HMODULE, W.LPWSTR, W.DWORD]
    process = kernel.OpenProcess(0x410, False, pid)
    if not process:
        return set()
    try:
        entries = (W.HMODULE * 1024)()
        needed = W.DWORD()
        if not psapi.EnumProcessModulesEx(process, entries, C.sizeof(entries), C.byref(needed), 3):
            return set()
        result = set()
        for item in entries[:min(1024, needed.value // C.sizeof(W.HMODULE))]:
            buffer = C.create_unicode_buffer(32768)
            if psapi.GetModuleFileNameExW(process, item, buffer, len(buffer)):
                result.add(buffer.value)
        return result
    finally:
        kernel.CloseHandle(process)


def main():
    project = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", type=Path, default=project / "dist/2DFastTurbo Python/2DFastTurbo Python.exe")
    parser.add_argument("--output", type=Path, default=project / "results/package-validation.json")
    args = parser.parse_args()
    exe = args.exe.resolve()
    folder = exe.parent
    work = Path(tempfile.mkdtemp(prefix="clean-runtime-", dir=project / "build"))
    system = Path(os.environ["SystemRoot"])
    environment = {k: v for k, v in os.environ.items()
                   if not k.upper().startswith(("CUDA", "PYTHON", "UV_", "VIRTUAL_ENV", "FASTTURBO"))}
    environment.update(PATH=str(system)+os.pathsep+str(system / "System32"), CUDA_CACHE_DISABLE="1")
    observed = set()
    cases = []
    for mode in ["headless", "gui"]:
        output = work / f"{mode}.json"
        spectrum = work / f"{mode}.c64"
        command = [str(exe), "--benchmark" if mode == "headless" else "--gui-benchmark",
            "--resolution", "128", "--k0", "10", "--reynolds", "50000", "--seed", "2", "--fixed-dt", "0.001",
            "--until", "0.125", "--output", str(output), "--spectrum", str(spectrum)]
        if mode == "gui":
            command += ["--gui-warmup-seconds", "0.1", "--export", str(work / "field.png")]
        before = {str(p.relative_to(folder)): p.stat().st_size for p in folder.rglob("*") if p.is_file()}
        with subprocess.Popen(command, cwd=work, env=environment) as child:
            deadline = time.monotonic()+120
            while child.poll() is None:
                observed.update(modules(child.pid))
                if time.monotonic()>deadline:
                    child.kill()
                    raise RuntimeError("Frozen application timed out")
                time.sleep(.02)
            assert child.returncode == 0, output.read_text() if output.exists() else child.returncode
        report = json.loads(output.read_text())
        assert report["stats"]["steps"] == 125 and report["stats"]["is_finite"]
        assert abs(report["stats"]["simulation_time"]-.125)<1e-12
        if mode == "gui":
            assert report["passed"] and report["presentations"]>0
            assert (work / "field.png").read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
        after = {str(p.relative_to(folder)): p.stat().st_size for p in folder.rglob("*") if p.is_file()}
        assert before == after, "Package wrote into its installation directory"
        cases.append({"mode": mode, "passed": True, "steps": report["stats"]["steps"],
                      "spectrum_sha256": hashlib.sha256(spectrum.read_bytes()).hexdigest()})
    assert cases[0]["spectrum_sha256"] == cases[1]["spectrum_sha256"]
    native = project / "build/windows/reference/2DFastTurbo.exe"
    native_env = {**environment, "PATH": str(project / "src/fastturbo/_native")+os.pathsep+environment["PATH"]}
    subprocess.run([str(native), "--benchmark", "--resolution", "128", "--k0", "10", "--reynolds", "50000", "--seed", "2",
        "--fixed-dt", "0.001", "--until", "0.125", "--spectrum", str(work / "reference.c64")],
        env=native_env, cwd=work, check=True, capture_output=True, timeout=60)
    assert (work / "reference.c64").read_bytes() == (work / "headless.c64").read_bytes()
    assert observed, "No loaded modules were observed"
    defender = Path(os.environ["ProgramData"]) / "Microsoft/Windows Defender"
    unexpected = [p for p in observed if not any(Path(p).is_relative_to(root) for root in (folder, system, defender))]
    assert not unexpected, unexpected
    result = {"passed": True, "cases": cases, "bitwise_native_agreement": True,
        "environment": "Windows-only PATH, no Python/CUDA development variables, CUDA cache disabled, empty working directory",
        "installation_unchanged": True, "loaded_modules": sorted(observed), "unexpected_modules": unexpected,
        "exe_sha256": hashlib.sha256(exe.read_bytes()).hexdigest(), "work_directory": str(work)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2)+"\n")
    print(json.dumps({k:v for k,v in result.items() if k != "loaded_modules"}, indent=2))


if __name__ == "__main__":
    main()
