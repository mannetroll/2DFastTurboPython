# Linux window close verification — 2026-09-17

Linux inherited the macOS close handler, which hid the window, ignored the close
event, and left the native simulation running. Linux has no Dock or tray reopen
mechanism in this application.

The Linux close handler now saves geometry, stops polling, destroys the native
controller and its workers, accepts the close event, and quits Qt. File → Close
Window (Ctrl+W) uses the same handler. Minimizing still permits simulation work.
The existing Windows and macOS lifecycle behavior is preserved.

## Verification

- `QT_QPA_PLATFORM=xcb uv run pytest -q`: **53 passed, 12 skipped**, 42.75 s.
  The skips are Windows-only tests. This includes Linux numerical/reference
  parity, rendering parity, worker lifecycle, and GUI controls. Two new subprocess
  tests close a running and presenting GPU simulation through the window and
  menu paths, then require event-loop exit and a closed native controller.
  See [pytest.xml](pytest.xml) and [Windows trajectory parity](windows-parity.json).
- `uv run ctest --test-dir build/linux --output-on-failure`: **1/1 passed**.
- Launched the actual command `QT_QPA_PLATFORM=xcb uv run 2dfastturbo` with its
  default N=2048 configuration. PID **55615** appeared on the physical RTX 3090
  as **C+G**, using **740 MiB**; total GPU utilization was **100%**.
- Sent `WM_DELETE_WINDOW`, the X11 protocol request used by the window manager's
  X button. The launch command exited with code **0** after **0.164 s**. The
  application PID disappeared from both `/proc` and `nvidia-smi`.
  See [close result](manual-close.json), [before](nvidia-smi-before-close.txt),
  and [after](nvidia-smi-after-close.txt).
- `git diff --check`: passed. No commit or push.

The initial external verification harness only searched uv's main thread for
child processes and missed the GUI spawned by another thread. After correcting
the harness to inspect every thread's children, the normal-launch check above
passed. This required no additional application changes.

No packages, CUDA settings, numerical tolerances, or renderer paths changed for
this fix. Earlier clean-build and benchmark evidence remains in
[linux-clean-build](../linux-clean-build/REPORT.md).

Source changes for this fix: `app.py`, `check_gui.py`, `test_linux.py`,
`test-linux.sh`, and `LINUX_PORT.md`. The default compute-only test command now
excludes the `gui` marker so the new GUI lifecycle tests require `--gui`.
Previously uncommitted CUDA/clean-build changes remain present. See
[git status](git-status.txt) for the complete working-tree status.
