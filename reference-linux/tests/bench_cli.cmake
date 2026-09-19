# CLI and missing-device checks run without a GPU or any scripting runtime.
execute_process(COMMAND "${CMAKE_COMMAND}" -E env CUDA_VISIBLE_DEVICES= "${EXE}" --help
  RESULT_VARIABLE status OUTPUT_VARIABLE output ERROR_VARIABLE error)
if(NOT status EQUAL 0 OR NOT output MATCHES "--seconds" OR NOT output MATCHES "no Python")
  message(FATAL_ERROR "Headless help failed: ${status} ${output} ${error}")
endif()
foreach(args IN ITEMS "--seconds;0" "--seconds;-1" "--seconds;nan" "--seconds;inf"
    "--seconds;1e999" "--seconds;0.1junk" "--seconds=" "--sizes;15" "--sizes;33"
    "--sizes;16386" "--sizes;32junk" "--sizes;32;--sizes" "--device;-1"
    "--device;999999999999999999999" "--warmup;-1" "--warmup;4097" "--json=yes"
    "--unknown" "--sizes" "--seconds" "--device" "--warmup")
  execute_process(COMMAND "${CMAKE_COMMAND}" -E env CUDA_VISIBLE_DEVICES= "${EXE}" ${args}
    RESULT_VARIABLE status OUTPUT_VARIABLE output ERROR_VARIABLE error)
  if(NOT status EQUAL 2 OR output OR error MATCHES "CUDA driver initialization")
    message(FATAL_ERROR "Invalid CLI was not rejected before CUDA: ${args}: ${status} ${output} ${error}")
  endif()
endforeach()
execute_process(COMMAND "${CMAKE_COMMAND}" -E env --unset=DISPLAY --unset=WAYLAND_DISPLAY
    --unset=XDG_RUNTIME_DIR --unset=LD_LIBRARY_PATH CUDA_VISIBLE_DEVICES=
    "${EXE}" --sizes 32 34 --seconds=.001 --warmup=0 --json
  RESULT_VARIABLE status OUTPUT_VARIABLE output ERROR_VARIABLE error)
if(NOT status EQUAL 1)
  message(FATAL_ERROR "Missing CUDA did not return failure: ${status} ${output} ${error}")
endif()
string(JSON count LENGTH "${output}" results)
if(NOT count EQUAL 2)
  message(FATAL_ERROR "Failed cases did not continue: ${output}")
endif()
foreach(i RANGE 0 1)
  string(JSON result GET "${output}" results ${i} status)
  string(JSON reason GET "${output}" results ${i} reason)
  if(NOT result STREQUAL "failed" OR NOT reason MATCHES "CUDA|cuda")
    message(FATAL_ERROR "Missing-device result is incorrect: ${output}")
  endif()
endforeach()
