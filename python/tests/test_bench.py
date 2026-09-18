"""CLI/orchestration checks; only the explicitly marked smoke tests need a GPU."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from fastturbo import bench
from fastturbo import main as production
from fastturbo.build_info import vkfft_version


def test_defaults_and_options():
    args = bench.parser().parse_args([])
    assert (args.sizes, args.seconds, args.device, args.warmup, args.json) == ([2048, 4096], 20.0, 0, 8, False)
    args = bench.parser().parse_args(["--sizes", "32", "2048", "--seconds", ".01", "--warmup", "0", "--device", "2", "--json"])
    assert (args.sizes, args.seconds, args.device, args.warmup, args.json) == ([32, 2048], .01, 2, 0, True)


@pytest.mark.parametrize("arguments", [
    ["--seconds", x] for x in ["0", "-1", "nan", "inf", "-inf"]
] + [["--sizes", x] for x in ["15", "33", "16386"]]
  + [["--device", "-1"], ["--warmup", "-1"], ["--warmup", "4097"]])
def test_invalid_arguments(arguments):
    with pytest.raises(SystemExit) as error:
        bench.parser().parse_args(arguments)
    assert error.value.code == 2


@pytest.mark.parametrize("system,expected", [("Linux", "Example Linux"), ("Darwin", "macOS 15.7"), ("Windows", "Example Windows")])
def test_metadata(monkeypatch, tmp_path, system, expected):
    monkeypatch.setattr(bench.platform, "system", lambda: system)
    monkeypatch.setattr(bench.platform, "platform", lambda: "Example Windows")
    monkeypatch.setattr(bench.platform, "freedesktop_os_release", lambda: {"PRETTY_NAME": "Example Linux"})
    monkeypatch.setattr(bench.platform, "mac_ver", lambda: ("15.7", (), ""))
    monkeypatch.setattr(bench.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(bench.socket, "gethostname", lambda: "test-host")
    monkeypatch.setattr(bench.subprocess, "check_output", lambda args, **kw: "abcdef1234\n" if "rev-parse" in args else " M changed.py\n")
    monkeypatch.setattr(bench, "NATIVE", tmp_path)
    (tmp_path / "build-manifest.json").write_text('{"vkfft_version":"1.3.4"}')
    data = bench.platform_metadata({"gpu": "Future GPU", "backend": "CUDA / VkFFT", "compute_capability": "9.0", "cuda_runtime_version": 13030, "cuda_driver_version": 13020})
    assert data["os"] == expected
    assert data["arch"] == "arm64" and data["hostname"] == "test-host"
    assert data["gpu"] == "Future GPU" and data["backend"] == "CUDA + VkFFT"
    assert data["cuda_runtime"] == "13.3" and data["cuda_driver"] == "13.2"
    assert data["vkfft_version"] == "1.3.4" and data["git_dirty"]
    assert data["git_commit"] == "abcdef1234"


def test_missing_optional_metadata(monkeypatch, tmp_path):
    def absent(*args, **kwargs):
        raise FileNotFoundError
    monkeypatch.setattr(bench.subprocess, "check_output", absent)
    monkeypatch.setattr(bench, "NATIVE", tmp_path)
    data = bench.platform_metadata({"gpu": "Apple GPU", "backend": "Metal / VkFFT", "unified_memory": True})
    assert data["git_commit"] is data["vkfft_version"] is data["cuda_runtime"] is None
    assert data["unified_memory"]


def test_memory_preflight():
    assert bench.memory_reason(4096, {"available_memory_bytes": 1024})
    assert bench.memory_reason(4096, {"available_memory_bytes": 24 * 2**30}) is None
    assert bench.memory_reason(4096, {"unified_memory": True, "physical_memory_bytes": 1024})
    # Recommended working set is not a hard allocation limit.
    assert bench.memory_reason(4096, {"recommended_working_set_bytes": 1024}) is None


@pytest.mark.parametrize("message", ["CUDA: out of memory", "cudaMalloc: memory allocation", "Cannot allocate spectrum (1024 bytes).", "VkFFT CUDA plan N=6144 batch=2 failed: 4001", "VkFFT plan creation failed with result 4025"])
def test_memory_failures(message):
    row = bench.failure(4096, message)
    assert row["status"] == "skipped" and row["padded_n"] == 6144
    assert "insufficient device memory" in row["reason"]
    assert message in row["reason"]


def native_report(n):
    return {"config": {"padded_resolution": n * 3 // 2}, "initial_stats": {"steps": 0},
            "stats": {"steps": 16, "is_finite": True}, "seconds": 2.04,
            "milliseconds_per_step": 127.5, "steps_per_s": 16 / 2.04, "initialization_seconds": .4}


def test_reuses_production_harness(monkeypatch):
    args = bench.parser().parse_args(["--sizes", "4096", "--device", "2", "--seconds", "2"])
    def hardware():
        assert os.environ["FASTTURBO_DEVICE"] == "2"
        return {"gpu": "Future GPU"}
    def run(options):
        assert (options.resolution, options.wall_seconds, options.batch, options.warmup) == (4096, 2, 8, 8)
        assert (options.integrator, options.operator, options.k0, options.seed, options.cfl) == ("etdrk4", "stress", 10, 2, 3)
        assert options.snapshot_hz == 0 and options.fixed_dt is None
        return native_report(options.resolution)
    monkeypatch.setenv("FASTTURBO_DEVICE", "0")
    monkeypatch.setattr(bench, "get_backend_info", hardware)
    monkeypatch.setattr(bench, "run_benchmark", run)
    result = bench.run_case(args)["result"]
    assert result["steps"] == 16 and result["padded_n"] == 6144
    assert result["ms_per_step"] == 127.5 and result["status"] == "ok"


def test_preflight_prevents_allocation(monkeypatch):
    monkeypatch.setenv("FASTTURBO_DEVICE", "0")
    monkeypatch.setattr(bench, "get_backend_info", lambda: {"available_memory_bytes": 1024})
    monkeypatch.setattr(bench, "run_benchmark", lambda _: pytest.fail("allocated after memory skip"))
    assert bench.run_case(bench.parser().parse_args([]))["result"]["status"] == "skipped"


def test_metadata_probe_does_not_benchmark(monkeypatch):
    monkeypatch.setenv("FASTTURBO_DEVICE", "0")
    def hardware():
        assert os.environ["FASTTURBO_DEVICE"] == "2"
        return {"gpu": "Selected GPU"}
    monkeypatch.setattr(bench, "get_backend_info", hardware)
    monkeypatch.setattr(bench, "run_benchmark", lambda _: pytest.fail("benchmark started during metadata probe"))
    args = bench.parser().parse_args(["--device", "2", "--_metadata-only"])
    assert bench.run_case(args) == {"hardware": {"gpu": "Selected GPU"}}


def test_native_errors_are_reported(monkeypatch):
    monkeypatch.setenv("FASTTURBO_DEVICE", "0")
    def bad():
        raise RuntimeError("invalid device ordinal")
    monkeypatch.setattr(bench, "get_backend_info", bad)
    row = bench.run_case(bench.parser().parse_args([]))["result"]
    assert row["status"] == "failed" and row["reason"] == "invalid device ordinal"


@pytest.mark.parametrize("as_json", [False, True])
def test_output_and_continue_after_skip(monkeypatch, capsys, as_json):
    visited = []
    def launch(args, n, *, metadata_only=False):
        if metadata_only:
            assert not as_json
            return {"hardware": {"gpu": "Example GPU", "backend": "Metal / VkFFT"}}
        visited.append(n)
        row = bench.failure(n, "out of memory") if n == 2048 else {
            "n": n, "padded_n": 6144, "status": "ok", "steps": 16,
            "seconds": 2.04, "ms_per_step": 127.5, "steps_per_second": 16 / 2.04}
        return {"hardware": {"gpu": "Example GPU", "backend": "Metal / VkFFT"}, "result": row}
    monkeypatch.setattr(bench, "launch_case", launch)
    assert bench.main(["--json"] if as_json else []) == 1
    assert visited == [2048, 4096]
    output = capsys.readouterr().out
    if as_json:
        report = json.loads(output)
        assert [r["status"] for r in report["results"]] == ["skipped", "ok"]
        assert report["timestep_ratio_4096_2048"] is None
        assert report["target_seconds"] == 20
    else:
        assert "SKIPPED: insufficient device memory" in output
        assert "2048²" in output and "6144²" in output and "127.500" in output
        assert "4096/2048 timestep ratio" not in output


def test_success_output_scaling(monkeypatch, capsys):
    def launch(args, n, *, metadata_only=False):
        ms = 2 if n == 2048 else 10
        return {"hardware": {"gpu": "Example", "backend": "CUDA / VkFFT"}, "result": {
            "n": n, "padded_n": n * 3 // 2, "status": "ok", "steps": 20000 // ms,
            "seconds": 20, "ms_per_step": ms, "steps_per_second": 1000 / ms}}
    monkeypatch.setattr(bench, "launch_case", launch)
    assert bench.main([]) == 0
    output = capsys.readouterr().out
    assert "4096/2048 timestep ratio: 5.00x" in output
    assert "Retained      Padded      Steps    Seconds    ms/step    steps/s" in output


def test_header_and_rows_are_flushed_before_next_benchmark(monkeypatch):
    class Output(io.StringIO):
        flushed = ""
        def flush(self):
            self.flushed = self.getvalue()
    output = Output()
    monkeypatch.setattr(bench.sys, "stdout", output)
    visited = []
    def launch(args, n, *, metadata_only=False):
        if metadata_only:
            assert not visited
            return {"hardware": {"gpu": "Example GPU", "backend": "Metal / VkFFT"}}
        assert "GPU:        Example GPU" in output.flushed
        assert "Target:     20.0 s per case" in output.flushed
        assert "-" * 64 in output.flushed
        assert "4096²" not in output.flushed
        if visited:
            assert "2048²" in output.flushed
        else:
            assert "2048²" not in output.flushed
        visited.append(n)
        return {"hardware": {}, "result": {"n": n, "padded_n": n * 3 // 2,
            "status": "ok", "steps": 16, "seconds": 20, "ms_per_step": 1250,
            "steps_per_second": .8}}
    monkeypatch.setattr(bench, "launch_case", launch)
    assert bench.main([]) == 0
    assert visited == [2048, 4096]
    assert output.flushed.count("FastTurbo 2D Benchmark") == 1
    assert "4096/2048 timestep ratio: 1.00x" in output.flushed


def test_worker_process_failure(monkeypatch):
    monkeypatch.setattr(bench.subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(a, -9))
    result = bench.launch_case(bench.parser().parse_args([]), 4096)["result"]
    assert result["status"] == "failed" and "code -9" in result["reason"]


def test_existing_benchmark_entry_emits_report(monkeypatch, capsys):
    report = native_report(32)
    monkeypatch.setattr(production, "run_benchmark", lambda _: report)
    assert production.benchmark(production.parser().parse_args(["--benchmark"])) == 0
    assert json.loads(capsys.readouterr().out) == report


def test_vkfft_build_version(tmp_path):
    header = tmp_path / "vkFFT.h"
    header.write_text("static inline int VkFFTGetVersion() { return 10304; }")
    assert vkfft_version(header) == "1.3.4"


@pytest.mark.gpu
def test_real_cli_json_and_invalid_device():
    environment = {k: v for k, v in os.environ.items() if k not in {"DISPLAY", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR"}}
    command = [sys.executable, "-m", "fastturbo.bench", "--sizes", "32", "--seconds", ".03", "--json"]
    process = subprocess.run(command, env=environment, capture_output=True, text=True, timeout=120)
    assert process.returncode == 0, process.stderr
    data = json.loads(process.stdout)
    row = data["results"][0]
    assert row["status"] == "ok" and row["steps"] > 0 and row["steps"] % 8 == 0
    assert row["seconds"] >= .03 and row["native_report"]["gpu_seconds"] > 0
    assert row["native_report"]["stats"]["is_finite"]
    assert row["native_report"]["initial_stats"]["steps"] == 0
    process = subprocess.run(command + ["--device", "999999"], env=environment, capture_output=True, text=True, timeout=120)
    assert process.returncode == 1
    assert json.loads(process.stdout)["results"][0]["status"] == "failed"


@pytest.mark.gpu
def test_real_worker_without_gui_imports(tmp_path):
    environment = {k: v for k, v in os.environ.items() if k not in {"DISPLAY", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR", "LD_LIBRARY_PATH"}}
    script = '''
import importlib.abc, sys
from pathlib import Path
class NoGUI(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, *args):
        if fullname.startswith(('PySide6', 'shiboken6', 'fastturbo.app')):
            raise AssertionError('Benchmark imported GUI: ' + fullname)
sys.meta_path.insert(0, NoGUI())
from fastturbo.bench import main
assert main(['--sizes', '32', '--seconds', '.03', '--_case-file', sys.argv[1]]) == 0
if sys.platform == 'linux':
    maps = Path('/proc/self/maps').read_text()
    for name in ('libQt', 'libX11', 'libwayland', 'libGL.so', 'libEGL.so', 'libfastturbo_gl'):
        assert name not in maps, name
'''
    path = tmp_path / "result.json"
    subprocess.run([sys.executable, "-c", script, str(path)], env=environment, check=True, timeout=120)
    assert json.loads(path.read_text())["result"]["status"] == "ok"
