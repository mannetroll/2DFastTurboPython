"""Launch the Python GUI or run a matched, explicitly timed native batch workload."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

from .engine import Configuration, Engine, native_library


def parser():
    p = argparse.ArgumentParser(description="2DFastTurbo Python · native Metal or CUDA / VkFFT")
    modes = p.add_mutually_exclusive_group()
    modes.add_argument("--benchmark", "--headless", dest="benchmark", action="store_true", help="Headless batch benchmark (no Qt or display required)")
    modes.add_argument("--gui-benchmark", action="store_true", help="Visible GUI benchmark, then exit")
    p.add_argument("--resolution", type=int, default=2048, help="Retained N; nonlinear workspace M=3N/2")
    p.add_argument("--reynolds", type=float, default=None, help="Viscosity=1/Re")
    p.add_argument("--k0", type=int, default=10)
    p.add_argument("--seed", type=int, default=2)
    p.add_argument("--start-spectrum", choices=["PAO", "KM3"], default="PAO")
    p.add_argument("--cfl", type=float, default=None, help="Default 3; CNAB2 default 0.25")
    p.add_argument("--integrator", choices=["imexRK3", "ifHeunRK3", "etdrk4", "CNAB2"], default="etdrk4")
    p.add_argument("--operator", choices=["stress", "advective"], default="stress")
    p.add_argument("--fixed-dt", type=float)
    ends = p.add_mutually_exclusive_group()
    ends.add_argument("--steps", type=int, default=None)
    ends.add_argument("--until", type=float)
    ends.add_argument("--turnovers", type=float)
    ends.add_argument("--wall-seconds", type=float)
    p.add_argument("--batch", type=int, default=None, help="1…32; headless default 8, GUI adaptive")
    p.add_argument("--warmup", type=int, default=8)
    p.add_argument("--snapshot-hz", type=float, default=0)
    p.add_argument("--initial", type=Path)
    p.add_argument("--spectrum", type=Path)
    p.add_argument("--fields", type=Path)
    p.add_argument("--output", type=Path)
    p.add_argument("--export", type=Path, help="Export completed field as PNG")
    p.add_argument("--display", choices=["vorticity", "enstrophy", "speed"], default="vorticity")
    p.add_argument("--palette", choices=["inferno", "ember", "aurora", "glacier"], default="ember")
    p.add_argument("--color-range", choices=["sigma", "exposure"], default="sigma")
    p.add_argument("--sigma", type=float, default=2.5)
    p.add_argument("--exposure", type=float, default=1.2)
    p.add_argument("--canvas-size", type=int, default=640)
    p.add_argument("--gui-warmup-seconds", type=float, default=2)
    return p


def emit(result, output):
    data = json.dumps(result, indent=2, allow_nan=False) + "\n"
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(data)
    print(data, end="", flush=True)


def config_from(args):
    return Configuration(resolution=args.resolution, reynolds=args.reynolds if args.reynolds is not None else Configuration().reynolds,
        k0=args.k0, seed=args.seed, cfl=args.cfl if args.cfl is not None else (.25 if args.integrator == "CNAB2" else 3), integrator=args.integrator,
        operator=args.operator, fixed_dt=args.fixed_dt, start_spectrum=args.start_spectrum)


def run_benchmark(args):
    """Shared headless harness; all numerical work stays in the native Engine.

    advance() and inspect() wait for GPU completion on both CUDA and Metal.
    Keep this timing definition shared with the existing --benchmark command.
    """
    initializing = time.perf_counter()
    config = config_from(args)
    with Engine(config, initial=args.initial) as engine:
        if args.warmup:
            engine.advance(args.warmup)
            if args.snapshot_hz or args.fields:
                engine.export(snapshot=True)
            engine.reset(initial=args.initial)
        initial = engine.inspect()
        target = args.until
        if args.turnovers is not None:
            target = args.turnovers * initial["initial_turnover_time"]
        count_steps, batch = args.steps if args.steps is not None else 200, args.batch or 8
        stats = initial
        batches = snapshots = 0
        gpu_seconds = snapshot_seconds = final_batch = 0.0
        next_snapshot = 0.0
        start = time.perf_counter()
        initialization_seconds = start - initializing
        def continuing():
            if args.wall_seconds is not None:
                return time.perf_counter() - start < args.wall_seconds
            if target is not None:
                return stats["simulation_time"] < target - 32 * max(abs(target), sys.float_info.min) * sys.float_info.epsilon
            return stats["steps"] - initial["steps"] < count_steps
        while continuing():
            batch_start = time.perf_counter()
            count = min(batch, count_steps - stats["steps"]) if target is None and args.wall_seconds is None else batch
            before = stats
            stats = engine.advance(count, until=target)
            batches += 1
            gpu_seconds += stats["gpu_milliseconds"] / 1000
            if not stats["is_finite"] or stats["steps"] <= before["steps"] or stats["simulation_time"] <= before["simulation_time"]:
                raise RuntimeError("Solver stopped making finite progress")
            elapsed = time.perf_counter() - start
            if args.snapshot_hz and elapsed >= next_snapshot:
                snap_start = time.perf_counter()
                engine.export(snapshot=True)
                snapshot_seconds += time.perf_counter() - snap_start
                snapshots += 1
                next_snapshot = elapsed + 1 / args.snapshot_hz
            final_batch = time.perf_counter() - batch_start
        inspection_start = time.perf_counter()
        stats = engine.inspect()
        finish = time.perf_counter()
        seconds = finish - start
        steps, simulated = stats["steps"] - initial["steps"], stats["simulation_time"] - initial["simulation_time"]
        frame = engine.export(spectrum=args.spectrum, fields=args.fields, png=args.export, snapshot=bool(args.fields or args.export),
                              display=args.display, palette=args.palette, color_range=args.color_range, sigma=args.sigma, exposure=args.exposure)
        report = {"schema_version": 1, "solver": "FastTurbo Python", "mode": "headless-with-snapshots" if args.snapshot_hz else "headless",
                  "config": {**config.payload(), "padded_resolution": config.padded_resolution, "batch": batch},
                  "initial_stats": initial, "stats": stats, "frame": frame,
                  "seconds": seconds, "initialization_seconds": initialization_seconds,
                  "simtime_per_s": simulated / max(seconds, 1e-12),
                  "steps_per_s": steps / max(seconds, 1e-12),
                  "milliseconds_per_step": seconds * 1000 / max(steps, 1),
                  "turnovers_per_s": simulated / max(initial["initial_turnover_time"] * seconds, 1e-12),
                  "gpu_seconds": gpu_seconds, "gpu_ms_per_step": gpu_seconds * 1000 / max(steps, 1),
                  "batches": batches, "snapshots": snapshots, "snapshot_seconds": snapshot_seconds,
                  "snapshot_hz": args.snapshot_hz, "warmup_steps": args.warmup,
                  "target_wall_seconds": args.wall_seconds, "target_time": target,
                  "final_batch_seconds": final_batch, "final_inspection_seconds": finish - inspection_start,
                  "spectrum_normalization": "fourier-series", "vorticity_convention": "omega = dx(v) - dy(u)",
                  "initial_source": "external-physical-field" if args.initial else "seeded-" + config.start_spectrum.lower(),
                  "initial_sha256": hashlib.sha256(args.initial.read_bytes()).hexdigest() if args.initial else None,
                  "hardware": native_library().info, "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    return report


def benchmark(args):
    emit(run_benchmark(args), args.output)
    return 0


def gui(args):
    from PySide6.QtCore import QTimer
    from .app import MainWindow, create_application
    app = create_application()
    config = config_from(args).payload()
    if args.reynolds is None:
        # Keep the GUI's calibrated initial Re without enabling regulation.
        config.pop("reynolds")
    window = MainWindow(config=config,
                        canvas_size=args.canvas_size, restore=not args.gui_benchmark)
    window.native.call("set", display=args.display, palette=args.palette, color_range=args.color_range,
                       sigma=args.sigma, exposure=args.exposure)
    if args.initial:
        window.native.call("benchmark", initial=str(args.initial))
    app.aboutToQuit.connect(window.shutdown)
    window.show()
    if args.gui_benchmark:
        driver = GUIBenchmark(window, args, app)
        driver.start()
    return app.exec()


class GUIBenchmark:
    def __init__(self, window, args, app):
        from PySide6.QtCore import QTimer
        self.window, self.args, self.app = window, args, app
        self.phase = "ready"
        self.deadline = time.perf_counter() + 120
        self.timer = QTimer(window)
        self.timer.setInterval(20)
        self.timer.timeout.connect(self.tick)
        self.warm_started = 0
        self.completed_at = None

    def start(self):
        self.timer.start()

    def tick(self):
        try:
            self._tick()
        except Exception as error:
            self.timer.stop()
            emit({"passed": False, "error": str(error), "phase": self.phase}, self.args.output)
            self.app.exit(2)

    def _tick(self):
        state = self.window.native.call()
        now = time.perf_counter()
        if state["error_message"]:
            raise RuntimeError(state["error_message"])
        if now > self.deadline:
            raise RuntimeError("Timed out waiting for " + self.phase)
        if self.phase == "ready" and state["ready"] and state["presentations"] > 0:
            self.warm_started = now
            self.phase = "warming"
        elif self.phase == "warming" and now - self.warm_started >= self.args.gui_warmup_seconds:
            self.window.native.call("running", value=False)
            self.phase = "pausing"
        elif self.phase == "pausing" and state["settled"]:
            until = self.args.until
            if self.args.turnovers is not None:
                until = self.args.turnovers * state["stats"]["initial_turnover_time"]
            wall = self.args.wall_seconds if self.args.wall_seconds is not None else (10 if until is None else None)
            self.window.native.call("benchmark", until=until, wall_seconds=wall, batch=self.args.batch,
                                    initial=str(self.args.initial) if self.args.initial else None,
                                    spectrum=str(self.args.spectrum) if self.args.spectrum else None)
            self.window.native.call("running", value=True)
            self.window.native.call("reset")
            self.window.poll_calls = 0
            self.window.poll_seconds = 0
            self.phase = "measuring"
            self.deadline = now + max(600, (wall or 0) + 120)
        elif self.phase == "measuring":
            if not state.get("canvas", {}).get("visible", False):
                raise RuntimeError("Benchmark window was hidden")
            if state["ready"] and not state["running"] and state["settled"]:
                if self.completed_at is None:
                    self.completed_at = now
                if now - self.completed_at < 0.25:
                    return  # Let the final native drawable present before reporting.
                self.timer.stop()
                seconds, stats = state["elapsed_seconds"], state["stats"]
                if seconds <= 0 or not stats["is_finite"] or state["presentations"] <= 0:
                    raise RuntimeError("Benchmark completed without a finite presented field")
                if self.args.export or self.args.fields:
                    self.window.native.call("export", png=str(self.args.export) if self.args.export else None,
                                            fields=str(self.args.fields) if self.args.fields else None)
                presentation_seconds = state.get("rendering", {}).get("presentation_seconds", seconds)
                intervals = state["presentations"] - (1 if "rendering" in state else 0)
                result = {"passed": True, "mode": "gui-benchmark", "solver": "FastTurbo Python", **state,
                          "seconds": seconds, "simulation_time_per_second": stats["simulation_time"] / seconds,
                          "steps_per_second": stats["steps"] / seconds,
                          "presentation": {"frames": state["presentations"],
                                           "seconds": presentation_seconds,
                                           "presented_frames_per_second": intervals / max(presentation_seconds, 1e-12)},
                          "python_ui_poll_seconds": self.window.poll_seconds, "python_ui_poll_calls": self.window.poll_calls,
                          "python_ui_poll_wall_percent": 100 * self.window.poll_seconds / seconds}
                emit(result, self.args.output)
                self.app.quit()


def main(argv=None):
    p = parser()
    args = p.parse_args(argv)
    if args.batch is not None and not 1 <= args.batch <= 32:
        p.error("--batch must be 1…32")
    if not 0 <= args.warmup <= 4096 or not 0 <= args.snapshot_hz <= 240:
        p.error("Invalid warmup or snapshot frequency")
    if args.steps is not None and args.steps < 0:
        p.error("--steps must be nonnegative")
    for key in ("until", "turnovers", "wall_seconds"):
        value = getattr(args, key)
        if value is not None and (not __import__("math").isfinite(value) or value < 0 or (key == "wall_seconds" and value == 0)):
            p.error(f"Invalid --{key.replace('_', '-')}")
    if args.gui_benchmark and args.steps is not None:
        p.error("GUI timing uses --until, --turnovers, or --wall-seconds")
    try:
        return benchmark(args) if args.benchmark else gui(args)
    except Exception as error:
        print(f"2DFastTurbo: {error}", file=sys.stderr)
        if args.output:
            emit({"passed": False, "error": str(error)}, args.output)
        return 2
