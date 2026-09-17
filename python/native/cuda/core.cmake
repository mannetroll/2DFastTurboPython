# Shared Linux targets: no Python bridge, controller or presentation libraries.
# Keep the production Windows numerical source files authoritative.
find_package(CUDAToolkit 13.0 REQUIRED)
add_library(cuda_platform STATIC "${CMAKE_CURRENT_LIST_DIR}/../linux/cuda_loader.cpp")
target_include_directories(cuda_platform PUBLIC "${CMAKE_CURRENT_LIST_DIR}/../linux")
target_link_libraries(cuda_platform PUBLIC CUDA::cuda_driver CUDA::nvrtc ${CMAKE_DL_LIBS})
add_library(turbo_core STATIC
  "${FASTTURBO_WINDOWS}/src/solver.cu"
  "${FASTTURBO_WINDOWS}/src/fft.cpp"
  "${CMAKE_CURRENT_LIST_DIR}/legacy_pao.cpp"
  "${CMAKE_CURRENT_LIST_DIR}/legacy_cnab2.cu")
target_include_directories(turbo_core PUBLIC "${FASTTURBO_WINDOWS}/src" "${CMAKE_CURRENT_LIST_DIR}")
target_include_directories(turbo_core PRIVATE "${FASTTURBO_VKFFT}")
target_compile_options(turbo_core PRIVATE
  $<$<COMPILE_LANGUAGE:CUDA>:--fmad=false>
  $<$<COMPILE_LANGUAGE:CXX>:-ffp-contract=off>)
target_link_libraries(turbo_core PUBLIC CUDA::cudart CUDA::cuda_driver CUDA::nvrtc)
set_target_properties(turbo_core cuda_platform PROPERTIES POSITION_INDEPENDENT_CODE ON)
