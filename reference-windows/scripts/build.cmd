@echo off
setlocal
call "%~1\VC\Auxiliary\Build\vcvars64.bat" >nul
if errorlevel 1 exit /b 1
set "PATH=%~2\bin;%PATH%"
set "CUDA_PATH=%~2"
set "TURBO_COMPILER=%VCToolsInstallDir:\=/%bin/Hostx64/x64/cl.exe"
set "TURBO_ARCHS=86"
if "%~7"=="ON" set "TURBO_ARCHS=75-real;80-real;86-real;89-real;120-real;120-virtual"
cmake -S "%~3" -B "%~6" -G Ninja "-DCMAKE_BUILD_TYPE=%~4" "-DCMAKE_CXX_COMPILER=%TURBO_COMPILER%" "-DCMAKE_CUDA_HOST_COMPILER=%TURBO_COMPILER%" "-DCMAKE_CUDA_COMPILER=%~2/bin/nvcc.exe" "-DTURBO_BUILD_GUI=%~5" "-DTURBO_DISTRIBUTION=%~7" "-DCMAKE_CUDA_ARCHITECTURES=%TURBO_ARCHS%"
if errorlevel 1 exit /b 1
cmake --build "%~6" --parallel 4
exit /b %errorlevel%
