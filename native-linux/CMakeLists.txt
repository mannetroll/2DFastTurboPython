# Thin desktop host. The numerical target is already built by reference-linux.
find_package(CUDAToolkit 13.0 REQUIRED)
find_package(OpenGL REQUIRED)
find_package(X11 REQUIRED)
find_package(PNG REQUIRED)
include(FetchContent)
FetchContent_Declare(imgui
  URL https://codeload.github.com/ocornut/imgui/tar.gz/refs/tags/v1.92.5
  URL_HASH SHA256=0eb50fe9aeba1a51f96b5843c7f630a32ed2e9362d693c61b87e4fa870cf826d
  DOWNLOAD_EXTRACT_TIMESTAMP TRUE)
FetchContent_MakeAvailable(imgui)
add_library(fastturbo_imgui STATIC
  "${imgui_SOURCE_DIR}/imgui.cpp" "${imgui_SOURCE_DIR}/imgui_draw.cpp"
  "${imgui_SOURCE_DIR}/imgui_tables.cpp" "${imgui_SOURCE_DIR}/imgui_widgets.cpp"
  "${imgui_SOURCE_DIR}/backends/imgui_impl_opengl3.cpp")
target_include_directories(fastturbo_imgui PUBLIC "${imgui_SOURCE_DIR}" "${imgui_SOURCE_DIR}/backends")
target_link_libraries(fastturbo_imgui PRIVATE ${CMAKE_DL_LIBS})

set(FASTTURBO_WINDOWS "${PROJECT_SOURCE_DIR}/reference-windows")
include("${PROJECT_SOURCE_DIR}/python/native/cuda/controller.cmake")
# Presentation cadence only. These guarded substitutions affect this target's
# generated controller, never the Python/Windows controller or solver sources.
adapt(controller.hpp "void visible(bool value);" "void visible(bool value);\n    void visualizationInterval(double seconds);")
adapt(controller.hpp "int singleSteps=0;" "int singleSteps=0;\n        double frameInterval=1./60;")
adapt(controller.cpp "void Controller::velocity(bool v)" "void Controller::visualizationInterval(double seconds){\n    if(!std::isfinite(seconds)||seconds<.004||seconds>10)throw std::invalid_argument(\"Invalid visualization interval.\");\n    std::lock_guard lock(mutex);request.frameInterval=seconds;++request.frameVersion;changed.notify_one();\n}\nvoid Controller::velocity(bool v)")
adapt(controller.cpp "needFrame=true;frameVersion=r.frameVersion;" "needFrame=true;nextFrame=0;frameVersion=r.frameVersion;")
adapt(controller.cpp "completed+1./60" "completed+r.frameInterval")
adapt(controller.cpp "(std::floor((completed-nextFrame)*60)+1)/60" "(std::floor((completed-nextFrame)/r.frameInterval)+1)*r.frameInterval")
# Readiness also applies when snapshot publication is disabled.
adapt(controller.cpp "state.stats=stats;if(!request.regulated)" "state.ready=true;state.stats=stats;if(!request.regulated)")

# Compile the authoritative reference's clock, file I/O and report helpers.
# No new benchmark loop, initialization, or export implementation.
file(READ "${FASTTURBO_WINDOWS}/src/main.cpp" host)
string(FIND "${host}" "static Options parse(int argc,char** argv)" boundary)
if(boundary EQUAL -1)
  message(FATAL_ERROR "Reference host helper boundary changed; review native GUI adaptation")
endif()
string(SUBSTRING "${host}" 0 ${boundary} host)
foreach(header resources.hpp windows.h shellapi.h)
  string(REPLACE "#include \"${header}\"" "" host "${host}")
  string(REPLACE "#include <${header}>" "" host "${host}")
endforeach()
file(WRITE "${adapted}/host.cpp" "${host}\n}\n")
add_library(fastturbo_desktop_controller STATIC "${adapted}/controller.cpp" "${adapted}/host.cpp")
target_include_directories(fastturbo_desktop_controller PUBLIC "${adapted}")
target_link_libraries(fastturbo_desktop_controller PUBLIC turbo_core cuda_platform)
target_compile_options(fastturbo_desktop_controller PRIVATE -ffp-contract=off -Wall -Wextra)

add_executable(fastturbo-linux main.cpp x11_imgui.cpp
  "${PROJECT_SOURCE_DIR}/python/native/linux/renderer.cpp")
target_include_directories(fastturbo-linux PRIVATE "${PROJECT_SOURCE_DIR}/python/native/linux"
  "${PROJECT_SOURCE_DIR}/python/native/windows/vendor")
target_link_libraries(fastturbo-linux PRIVATE fastturbo_desktop_controller fastturbo_imgui
  OpenGL::GL X11::X11 PNG::PNG)
target_compile_options(fastturbo-linux PRIVATE -ffp-contract=off -Wall -Wextra -Wpedantic)
set_target_properties(fastturbo-linux PROPERTIES RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}"
  BUILD_RPATH "${CUDAToolkit_LIBRARY_DIR}" INSTALL_RPATH "${CUDAToolkit_LIBRARY_DIR}")
install(TARGETS fastturbo-linux RUNTIME DESTINATION bin COMPONENT native-gui)
install(FILES "${imgui_SOURCE_DIR}/LICENSE.txt" DESTINATION share/licenses/fastturbo-linux
  RENAME Dear-ImGui.txt COMPONENT native-gui)
install(FILES "${PROJECT_SOURCE_DIR}/python/LICENSE" DESTINATION share/licenses/fastturbo-linux
  RENAME FastTurbo.txt COMPONENT native-gui)
install(FILES "${PROJECT_SOURCE_DIR}/python/licenses/VkFFT-LICENSE.txt"
  "${PROJECT_SOURCE_DIR}/python/licenses/json-LICENSE.txt"
  DESTINATION share/licenses/fastturbo-linux COMPONENT native-gui)
if(BUILD_TESTING)
  add_test(NAME native_gui_cli COMMAND "${CMAKE_COMMAND}"
    -DEXE=$<TARGET_FILE:fastturbo-linux> -P "${CMAKE_CURRENT_SOURCE_DIR}/tests/cli.cmake")
  add_executable(native_controller_tests tests/controller.cpp)
  target_link_libraries(native_controller_tests PRIVATE fastturbo_desktop_controller)
  add_test(NAME native_controller COMMAND native_controller_tests)
  set_tests_properties(native_controller PROPERTIES TIMEOUT 180 RESOURCE_LOCK cuda)
  option(FASTTURBO_NATIVE_GUI_TESTS "Run native GUI tests on a real X11/XWayland display" OFF)
  if(FASTTURBO_NATIVE_GUI_TESTS)
    add_test(NAME native_gui_desktop COMMAND fastturbo-linux --self-test
      --output "${CMAKE_CURRENT_BINARY_DIR}/desktop-test.json")
    set_tests_properties(native_gui_desktop PROPERTIES TIMEOUT 180 RESOURCE_LOCK cuda LABELS gui)
  endif()
endif()
