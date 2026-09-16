cmake_minimum_required(VERSION 3.26)
project(FastTurboPythonLinux LANGUAGES CXX CUDA)
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CUDA_STANDARD 20)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)
find_package(CUDAToolkit 13.0 REQUIRED)
add_library(cuda_platform STATIC cuda_loader.cpp)
target_include_directories(cuda_platform PUBLIC "${CMAKE_CURRENT_SOURCE_DIR}")
target_link_libraries(cuda_platform PUBLIC CUDA::cuda_driver ${CMAKE_DL_LIBS})

# These are the SAME sources linked by the Windows backend, with no generated
# numerical adaptations. Win32/D3D/controller code is deliberately excluded.
add_library(turbo_core STATIC
  "${FASTTURBO_WINDOWS}/src/solver.cu" "${FASTTURBO_WINDOWS}/src/fft.cpp")
target_include_directories(turbo_core PUBLIC "${FASTTURBO_WINDOWS}/src")
target_include_directories(turbo_core PRIVATE "${FASTTURBO_VKFFT}")
target_compile_options(turbo_core PRIVATE
  $<$<COMPILE_LANGUAGE:CUDA>:--fmad=false>
  $<$<COMPILE_LANGUAGE:CXX>:-ffp-contract=off>)
target_link_libraries(turbo_core PUBLIC CUDA::cudart CUDA::cuda_driver CUDA::nvrtc)

add_library(fastturbo SHARED bridge.cpp)
target_include_directories(fastturbo PRIVATE .. ../windows/vendor)
target_compile_options(fastturbo PRIVATE -ffp-contract=off -Wall -Wextra)
target_link_libraries(fastturbo PRIVATE turbo_core cuda_platform)
# NVRTC finds its matching builtins next to itself. No LD_LIBRARY_PATH required.
set_target_properties(fastturbo PROPERTIES BUILD_RPATH "${CUDAToolkit_LIBRARY_DIR}")
file(WRITE "${CMAKE_CURRENT_BINARY_DIR}/core_tests.cpp"
  "#include \"cuda_loader.hpp\"\n#define main reference_core_main\n#include \"${FASTTURBO_WINDOWS}/tests/core_tests.cpp\"\n#undef main\nint main(){initializeCudaLoader();return reference_core_main();}\n")
add_executable(core_tests "${CMAKE_CURRENT_BINARY_DIR}/core_tests.cpp")
target_link_libraries(core_tests PRIVATE turbo_core cuda_platform)
enable_testing()
add_test(NAME core_lifecycle COMMAND core_tests)

# Independent CLI invocation of the original reference parser/benchmark. Only
# its Windows includes, resource handling and WinMain entry point are excluded.
file(READ "${FASTTURBO_WINDOWS}/src/main.cpp" reference_cli)
string(FIND "${reference_cli}" "static int run(int argc,char** argv)" entry)
if(entry EQUAL -1)
  message(FATAL_ERROR "Reference CLI entry changed; review Linux adaptation.")
endif()
string(SUBSTRING "${reference_cli}" 0 ${entry} reference_cli)
foreach(header resources.hpp windows.h shellapi.h)
  string(REPLACE "#include \"${header}\"" "" reference_cli "${reference_cli}")
  string(REPLACE "#include <${header}>" "" reference_cli "${reference_cli}")
endforeach()
string(APPEND reference_cli "\n#include \"cuda_loader.hpp\"\nint main(int argc,char** argv) {\n  try { initializeCudaLoader(); auto options=turbo::parse(argc,argv);\n    if(!options.benchmark) throw std::runtime_error(\"This reference harness requires --benchmark\");\n    return turbo::benchmark(options);\n  } catch(const std::exception& e) {std::cerr<<e.what()<<'\\n';return 2;}\n}\n")
file(WRITE "${CMAKE_CURRENT_BINARY_DIR}/reference_cli.cpp" "${reference_cli}")
add_executable(cuda_reference "${CMAKE_CURRENT_BINARY_DIR}/reference_cli.cpp")
target_link_libraries(cuda_reference PRIVATE turbo_core cuda_platform)
target_compile_options(cuda_reference PRIVATE -ffp-contract=off)
add_executable(device_test device_test.cu)
target_link_libraries(device_test PRIVATE CUDA::cudart_static)
set_target_properties(device_test PROPERTIES CUDA_RUNTIME_LIBRARY Static)
