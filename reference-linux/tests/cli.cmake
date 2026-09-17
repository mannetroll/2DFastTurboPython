# All errors must be rejected before any CUDA initialization. --help must also
# work on a host without a visible device. No scripting runtime is required.
execute_process(COMMAND "${CMAKE_COMMAND}" -E env CUDA_VISIBLE_DEVICES= "${EXE}" --help
  RESULT_VARIABLE status OUTPUT_VARIABLE output ERROR_VARIABLE error)
if(NOT status EQUAL 0 OR NOT output MATCHES "--benchmark")
  message(FATAL_ERROR "Headless help failed: ${error}")
endif()
foreach(args IN ITEMS "--n;31" "--steps;0" "--seed;-1" "--device;-1" "--warmup;-1"
    "--n;32junk" "--cfl;nan" "--fixed-dt;1e-100" "--k0;0" "--n;32;--n;64" "--unknown" "--n"
    "512;10000;10;1001;0.25;metal;100;PAO;CNAB2")
  execute_process(COMMAND "${EXE}" ${args} RESULT_VARIABLE status OUTPUT_QUIET ERROR_VARIABLE error)
  if(NOT status EQUAL 2 OR error MATCHES "CUDA driver initialization")
    message(FATAL_ERROR "Invalid CLI was not rejected before CUDA: ${args}: ${status} ${error}")
  endif()
endforeach()
execute_process(COMMAND "${CMAKE_COMMAND}" -E env CUDA_VISIBLE_DEVICES= "${EXE}" --n 32 --steps 1
  RESULT_VARIABLE status OUTPUT_VARIABLE output ERROR_VARIABLE error)
if(NOT status EQUAL 2 OR NOT error MATCHES "CUDA|cuda" OR output MATCHES "PASS")
  message(FATAL_ERROR "Missing CUDA device did not fail clearly: ${status} ${error}")
endif()
execute_process(COMMAND "${EXE}" --device 2147483647 --n 32 --steps 1
  RESULT_VARIABLE status OUTPUT_VARIABLE output ERROR_VARIABLE error)
if(NOT status EQUAL 2 OR NOT error MATCHES "CUDA device index out of range")
  message(FATAL_ERROR "Out-of-range CUDA device did not fail clearly: ${status} ${error}")
endif()
