"""Exercise actual Qt controls and the native pump/renderer on a visible desktop."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from fastturbo.app import MainWindow, create_application
from fastturbo.controls import RESOLUTIONS, WAVENUMBERS, FIELDS, METHODS, PALETTES
from fastturbo.engine import Configuration, NativeUI

p = argparse.ArgumentParser()
p.add_argument("--output", type=Path, default=Path("results/gui-check.json"))
p.add_argument("--all-resolutions", action="store_true")
args = p.parse_args()
args.output.parent.mkdir(parents=True, exist_ok=True)
app = create_application()
window = MainWindow(config={"resolution": 128}, canvas_size=640, restore=False)
window.show()
checks = {}


def state():
    app.processEvents()
    return window.native.call()


def wait(predicate, seconds=120):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        s = state()
        assert not s["error_message"], s["error_message"]
        if predicate(s):
            window.poll()
            return s
        QTest.qWait(10)
    raise AssertionError("Timed out waiting for native state: " + json.dumps(state()))


def check(name, condition):
    assert condition, name
    checks[name] = True


def pause():
    window.send("running", value=False)
    return wait(lambda s: s["ready"] and s["settled"])


try:
    probe = NativeUI()
    configured = probe.call("configure", integrator="imexRK3", cfl=3)
    check("explicit_cli_cfl_preserved", configured["config"]["cfl"] == 3)
    probe.close()
    s = wait(lambda s: s["ready"] and s["presentations"] > 3 and s["performance"]["simtime_per_s"] > 0)
    check("native_hdr_presented", s["presentations"] > 0)
    check("square_canvas", abs(s["canvas"]["width_points"] - s["canvas"]["height_points"]) <= 1)
    check("factory_defaults", not s["regulated"] and s["display"] == "vorticity" and s["palette"] == "ember" and s["sigma"] == 2.5 and s["exposure"] == 1.2)
    window.seed.setFocus()
    window.seed.setText("123")
    edit = next(action.menu() for action in window.menuBar().actions() if action.text() == "Edit")
    next(action for action in edit.actions() if action.text() == "Select All").trigger()
    check("edit_menu_targets_seed", window.seed.selectedText() == "123")
    window.seed.setText(s["config"]["seed"])
    window.cfl.slider.setFocus()
    before_running = s["running"]
    QTest.keyClick(window.cfl.slider, Qt.Key.Key_Space)
    s = wait(lambda s: s["settled"] and not s["running"])
    check("space_after_slider_focus", before_running and not s["running"])
    frozen = s["stats"]["steps"]
    frozen_perf = s["performance"]
    QTest.qWait(150)
    check("pause_freezes_completed_steps", state()["stats"]["steps"] == frozen)
    window.step_action.trigger()
    s = wait(lambda s: s["settled"] and s["stats"]["steps"] == frozen+1)
    check("single_step_exactly_one", s["stats"]["steps"] == frozen+1)
    check("single_step_retains_throughput", s["performance"]["simtime_per_s"] == frozen_perf["simtime_per_s"])
    window.send("reset")
    s = wait(lambda s: s["ready"] and s["settled"])
    check("reset_zeroes_run", s["stats"]["steps"] == 0 and s["stats"]["simulation_time"] == 0 and s["performance"]["simtime_per_s"] == 0)
    QTest.mouseClick(window.regulated, Qt.MouseButton.LeftButton)
    s = wait(lambda s: s["regulated"])
    # Regulation off holds current effective Re, then manual edits preserve state.
    held_re = s["effective_reynolds"]
    QTest.mouseClick(window.regulated, Qt.MouseButton.LeftButton)
    s = state()
    check("regulator_holds_re_when_disabled", not s["regulated"] and abs(s["effective_reynolds"]-held_re) < 1e-8)
    seq, generation = s["frame"]["sequence"], s["generation"]
    window.reynolds.slider.setValue(6667)
    window.cfl.slider.setValue(3000)
    window.send("flush")
    QTest.qWait(70)
    s = state()
    check("live_scalars_keep_generation_and_frame", s["generation"] == generation and s["frame"]["sequence"] == seq)
    check("cfl_maps_to_native", abs(s["config"]["cfl"] - 1.6) < 1e-6)
    check("re_maps_to_native", abs(s["effective_reynolds"] - 10**5.0001) < 1e-6)
    for title, method in METHODS:
        window.cfl.slider.setValue(10000)
        window.method.setCurrentIndex(window.method.findData(method))
        s = wait(lambda s: s["ready"] and s["settled"])
        check("method_"+method, s["config"]["integrator"] == method)
        if method != "etdrk4":
            check("rk3_cfl_cap_"+method, s["config"]["cfl"] <= 1.5)
    for form in ["advective", "stress"]:
        window.form.setCurrentIndex(window.form.findData(form))
        s = wait(lambda s: s["ready"] and s["settled"])
        check("form_"+form, s["config"]["operator"] == form)
    window.seed.setText(str(2**64-1))
    QTest.keyClick(window.seed, Qt.Key.Key_Return)
    s = wait(lambda s: s["ready"] and s["settled"])
    check("full_uint64_seed", s["config"]["seed"] == str(2**64-1))
    window.send("seed")
    s = wait(lambda s: s["ready"] and s["settled"])
    check("new_seed_wraps", s["config"]["seed"] == "0")
    for n in RESOLUTIONS if args.all_resolutions else [256, 128]:
        previous_n = window.resolution.currentData()
        window.resolution.setCurrentIndex(window.resolution.findData(n))
        s = wait(lambda s: s["ready"] and s["settled"])
        check(f"resolution_{n}_rebuild", s["config"]["resolution"] == n and s["frame"]["grid_size"] == n and s["config"]["padded_resolution"] == 3*n//2)
        if n != previous_n:
            check(f"resolution_{n}_recalibrates_manual_re", not s["regulated"] and abs(s["effective_reynolds"] - Configuration.calibrated_reynolds(n, s["config"]["k0"])) < 1e-8)
        window.send("step")
        s = wait(lambda s: s["ready"] and s["settled"] and s["stats"]["steps"] == 1)
        check(f"resolution_{n}_finite_step", s["stats"]["is_finite"])
    window.resolution.setCurrentIndex(window.resolution.findData(128))
    wait(lambda s: s["ready"] and s["settled"])
    window.k0.setCurrentIndex(window.k0.findData(8))
    s = wait(lambda s: s["ready"] and s["settled"])
    check("k0_reinitializes", s["config"]["k0"] == 8 and s["stats"]["steps"] == 0)
    check("k0_recalibrates_manual_re", not s["regulated"] and abs(s["effective_reynolds"] - Configuration.calibrated_reynolds(128, 8)) < 1e-8)
    frame_file = args.output.with_suffix(".f32")
    window.native.call("export", fields=str(frame_file))
    # Include velocity in every rendering test so the exported physical planes match.
    window.flow.setChecked(True)
    wait(lambda s: s["settled"] and s["frame"]["max_speed"] > 0)
    window.native.call("export", fields=str(frame_file))
    before = hashlib.sha256(frame_file.read_bytes()).hexdigest()
    for title, field in FIELDS:
        QTest.mouseClick(window.fields[field], Qt.MouseButton.LeftButton)
        for palette in PALETTES:
            QTest.mouseClick(window.palettes[palette], Qt.MouseButton.LeftButton)
            window.poll()
            s = state()
            check(f"display_{field}_{palette}", s["display"] == field and s["palette"] == palette)
    window.native.call("export", fields=str(frame_file))
    check("rendering_does_not_mutate_physical_state", hashlib.sha256(frame_file.read_bytes()).hexdigest() == before)
    window.fields["vorticity"].click()
    window.sigma.slider.setValue(7500)
    window.color_range.setCurrentIndex(window.color_range.findData("exposure"))
    window.exposure.slider.setValue(8000)
    window.color_range.setCurrentIndex(window.color_range.findData("sigma"))
    s = state()
    check("independent_sigma_exposure_values", s["sigma"] == 3 and abs(s["exposure"]-2.45)<1e-8)
    side = window.canvas.width()
    window.toggle_focus()
    QTest.qWait(100)
    s = state()
    check("focus_preserves_square_side", abs(s["canvas"]["width_points"]-side)<=1 and abs(s["canvas"]["width_points"]-s["canvas"]["height_points"])<=1)
    window.toggle_focus()
    QTest.qWait(100)
    check("controls_restored", window.inspector.isVisible() and window.metrics.isVisible())
    window.resize(window.width()+40, window.height())
    QTest.qWait(100)
    s = state()
    check("resize_square", abs(s["canvas"]["width_points"]-s["canvas"]["height_points"])<=1)
    window.send("running", value=True)
    window.resolution.setCurrentIndex(window.resolution.findData(256))
    window.send("running", value=False)
    s = wait(lambda s: s["ready"] and s["settled"])
    check("replacement_preserves_latest_pause", not s["running"] and s["stats"]["steps"]==0)
    window.close()
    QTest.qWait(100)
    check("close_hides_window", not window.isVisible())
    # Qt's Cocoa delegate sends this signal for Dock reopen, even if already active.
    app.applicationStateChanged.emit(Qt.ApplicationState.ApplicationActive)
    s = wait(lambda s: s["ready"] and s["settled"] and s["canvas"]["visible"])
    check("reopen_retains_run", s["stats"]["steps"]==0)
    window.send("running", value=True)
    s = wait(lambda s: s["stats"]["steps"]>20)
    window.showMinimized()
    QTest.qWait(250)
    hidden = state()
    QTest.qWait(250)
    advanced = state()
    check("minimized_simulation_continues", advanced["stats"]["steps"] > hidden["stats"]["steps"])
    window.reopen()
    pause()
    window.fields["vorticity"].click()
    window.palettes["ember"].click()
    window.flow.setChecked(False)
    window.sigma.slider.setValue(5000)
    QTest.qWait(150)
    window.native.call("export", png=str(args.output.with_name("gui-field.png")), pixel_size=800)
    if sys.platform == "win32":
        import ctypes
        native_windows = ctypes.WinDLL("user32")
        native_windows.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
        native_windows.SetWindowPos(int(window.winId()), ctypes.c_void_p(-1), 0, 0, 0, 0, 0x13)
        QTest.qWait(100)
        origin = window.mapToGlobal(QPoint(0, 0))
        screen = window.screen().grabWindow(0, origin.x(), origin.y(), window.width(), window.height())
    else:
        screen = app.primaryScreen().grabWindow(int(window.winId()))
    screen.save(str(args.output.with_name("gui-window.png")))
    result = {"passed": True, "checks": checks, "state": state()}
except Exception as error:
    result = {"passed": False, "error": str(error), "checks": checks, "state": state()}
finally:
    window.shutdown()
args.output.write_text(json.dumps(result, indent=2)+"\n")
print(json.dumps({k:v for k,v in result.items() if k!="state"}, indent=2))
raise SystemExit(0 if result["passed"] else 1)
