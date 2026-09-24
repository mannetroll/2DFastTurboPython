cmake_minimum_required(VERSION 3.26)
project(FastTurboPythonLinux LANGUAGES CXX CUDA)
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CUDA_STANDARD 20)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)
include("${CMAKE_CURRENT_SOURCE_DIR}/../cuda/core.cmake")

include("${CMAKE_CURRENT_SOURCE_DIR}/../cuda/controller.cmake")
add_library(fastturbo SHARED bridge.cpp ui.cpp "${adapted}/controller.cpp")
target_include_directories(fastturbo PRIVATE "${adapted}" .. ../windows/vendor)
target_compile_options(fastturbo PRIVATE -ffp-contract=off -Wall -Wextra)
target_link_libraries(fastturbo PRIVATE turbo_core cuda_platform)
target_link_options(fastturbo PRIVATE -Wl,--no-undefined)
# NVRTC finds its matching builtins next to itself. No LD_LIBRARY_PATH required.
set_target_properties(fastturbo PROPERTIES BUILD_RPATH "${CUDAToolkit_LIBRARY_DIR}")
# Use CMake's resolved runtime locations, including nonstandard CUDA layouts.
file(GENERATE OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/cuda-libraries.txt"
  CONTENT "$<TARGET_FILE:CUDA::cudart>\n$<TARGET_FILE:CUDA::nvrtc>\n")

# Optional presentation module. It is dlopened only for UI/PNG requests, so a
# compute-only machine never needs to load X11 or OpenGL libraries.
set(FASTTURBO_RENDERER "AUTO" CACHE STRING "Linux renderer: AUTO, ON, OFF")
set_property(CACHE FASTTURBO_RENDERER PROPERTY STRINGS AUTO ON OFF)
if(NOT FASTTURBO_RENDERER MATCHES "^(AUTO|ON|OFF)$")
  message(FATAL_ERROR "FASTTURBO_RENDERER must be AUTO, ON or OFF")
endif()
set(rendering 0)
if(NOT FASTTURBO_RENDERER STREQUAL "OFF")
  # Use the host's libGL ABI so Qt, CUDA and the renderer share one GLX dispatch
  # stack. Linking split GLVND libraries causes auditwheel to bundle private
  # copies, whose contexts the NVIDIA driver's CUDA interop cannot recognize.
  # libGL.so.1 is a manylinux system library and stays outside the wheel.
  set(OpenGL_GL_PREFERENCE LEGACY)
  find_package(OpenGL QUIET)
  find_package(X11 QUIET)
  find_package(PNG QUIET)
  if(OpenGL_FOUND AND OPENGL_gl_LIBRARY AND X11_FOUND AND PNG_FOUND)
    add_library(fastturbo_gl SHARED renderer.cpp)
    target_include_directories(fastturbo_gl PRIVATE "${FASTTURBO_WINDOWS}/src")
    target_link_libraries(fastturbo_gl PRIVATE CUDA::cudart OpenGL::GL X11::X11 PNG::PNG)
    target_compile_options(fastturbo_gl PRIVATE -Wall -Wextra)
    set(rendering 1)
  elseif(FASTTURBO_RENDERER STREQUAL "ON")
    message(FATAL_ERROR "Linux rendering requires OpenGL, X11 and libpng development files.")
  else()
    message(STATUS "Building headless only: OpenGL/X11/libpng development files not found")
  endif()
endif()
target_compile_definitions(fastturbo PRIVATE FASTTURBO_HAS_RENDERER=${rendering})
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
