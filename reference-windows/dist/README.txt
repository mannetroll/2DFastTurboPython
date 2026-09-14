2D FAST TURBO - Windows x64

Runtime prerequisites:
- Windows 10 or Windows 11, 64-bit.
- NVIDIA RTX GPU (Turing, Ampere, Ada or Blackwell).
- Compatible NVIDIA display driver with CUDA 13 support (R580 or newer).
- Sufficient GPU memory for the selected resolution; validated on RTX 3090 24 GB.

Open 2DFastTurbo.exe. The EXE can run by itself, from any working directory.
No CUDA Toolkit, Visual Studio, VC++ Redistributable or development tools
are required. Do not copy NVIDIA driver DLLs alongside the application.

Space: pause/resume. Right: single step. R: reset. F: focus.
Ctrl+S: save PNG. Ctrl+Shift+R: next seed. Ctrl+Q: quit.
Closing the window keeps computing in the notification area; click its icon
to reopen, or right-click the icon and choose Quit.

The application does not write required files into its installation folder.
Shader code, icon, color maps, defaults and third-party notices are embedded.
Windows supplies the system fonts. NVIDIA may maintain optional per-user caches.

To read the embedded third-party notices from PowerShell:
  .\2DFastTurbo.exe --licenses | Out-Host
