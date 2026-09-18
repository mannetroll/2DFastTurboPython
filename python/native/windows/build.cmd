@echo off
setlocal
call "%~1\VC\Auxiliary\Build\vcvars64.bat" >nul
if errorlevel 1 exit /b 1
set "PATH=%~2\bin;%~2\bin\x64;%PATH%"
set "CUDA_PATH=%~2"
set "TURBO_COMPILER=%VCToolsInstallDir:\=/%bin/Hostx64/x64/cl.exe"
rem cmd treats unquoted semicolons and commas as separators; transport lists with /.
set "TURBO_ARCHITECTURES=%~7"
set "TURBO_ARCHITECTURES=%TURBO_ARCHITECTURES:/=;%"
cmake -S "%~3" -B "%~4" -G Ninja -DCMAKE_BUILD_TYPE=Release "-DCMAKE_CXX_COMPILER=%TURBO_COMPILER%" "-DCMAKE_CUDA_HOST_COMPILER=%TURBO_COMPILER%" "-DCMAKE_CUDA_COMPILER=%~2/bin/nvcc.exe" "-DFASTTURBO_WINDOWS=%~5" "-DFASTTURBO_MACOS=%~6" "-DCMAKE_CUDA_ARCHITECTURES=%TURBO_ARCHITECTURES%" "-DTURBO_DISTRIBUTION=%~8" "-DFASTTURBO_VKFFT=%~9"
if errorlevel 1 exit /b 1
cmake --build "%~4" --parallel 4
exit /b %errorlevel%
