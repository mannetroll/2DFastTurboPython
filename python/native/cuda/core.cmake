# Shared Linux targets: no Python bridge, controller or presentation libraries.
# Keep the production Windows numerical source files authoritative.
find_package(CUDAToolkit 13.0 REQUIRED)
if(FASTTURBO_DISTRIBUTION)
  set(fastturbo_cudart CUDA::cudart_static)
  set(fastturbo_nvrtc CUDA::nvrtc_static CUDA::nvrtc_builtins_static CUDA::nvptxcompiler_static)
else()
  set(fastturbo_cudart CUDA::cudart)
  set(fastturbo_nvrtc CUDA::nvrtc)
endif()
add_library(cuda_platform STATIC "${CMAKE_CURRENT_LIST_DIR}/../linux/cuda_loader.cpp"
  "${CMAKE_CURRENT_LIST_DIR}/../linux/cuda_driver.cpp")
target_include_directories(cuda_platform PUBLIC "${CMAKE_CURRENT_LIST_DIR}/../linux")
target_link_libraries(cuda_platform PUBLIC ${fastturbo_nvrtc} ${CMAKE_DL_LIBS})
if(FASTTURBO_DISTRIBUTION)
  target_compile_definitions(cuda_platform PRIVATE FASTTURBO_STATIC_NVRTC=1)
  target_link_options(cuda_platform INTERFACE -static-libstdc++ -static-libgcc)
endif()
# Driver forwarding symbols are internal; do not interpose on other CUDA users.
set_target_properties(cuda_platform PROPERTIES CXX_VISIBILITY_PRESET hidden)
add_library(turbo_core STATIC
  "${FASTTURBO_WINDOWS}/src/solver.cu"
  "${FASTTURBO_WINDOWS}/src/fft.cpp"
  "${CMAKE_CURRENT_LIST_DIR}/legacy_pao.cpp"
  "${CMAKE_CURRENT_LIST_DIR}/legacy_cnab2.cu")
target_include_directories(turbo_core PUBLIC "${FASTTURBO_WINDOWS}/src" "${CMAKE_CURRENT_LIST_DIR}")
target_compile_definitions(turbo_core PUBLIC FASTTURBO_ENABLE_CNAB2=1)
target_include_directories(turbo_core PRIVATE "${FASTTURBO_VKFFT}")
target_compile_options(turbo_core PRIVATE
  $<$<COMPILE_LANGUAGE:CUDA>:--fmad=false>
  $<$<COMPILE_LANGUAGE:CXX>:-ffp-contract=off>)
target_link_libraries(turbo_core PUBLIC ${fastturbo_cudart} ${fastturbo_nvrtc} cuda_platform)
set_target_properties(turbo_core cuda_platform PROPERTIES POSITION_INDEPENDENT_CODE ON)
