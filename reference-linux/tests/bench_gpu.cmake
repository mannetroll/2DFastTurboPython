# Exercise real GPU timing, reset, odd padded sizes, process isolation and JSON.
# Clear display, loader and command search paths; none is a runtime dependency.
set(run "${CMAKE_COMMAND}" -E env --unset=DISPLAY --unset=WAYLAND_DISPLAY
  --unset=XDG_RUNTIME_DIR --unset=LD_LIBRARY_PATH PATH= "${EXE}")
foreach(warmup IN ITEMS 0 8)
  execute_process(COMMAND ${run} --sizes 32 34 --seconds .03 --warmup ${warmup} --json
    RESULT_VARIABLE status OUTPUT_VARIABLE output ERROR_VARIABLE error)
  if(NOT status EQUAL 0)
    message(FATAL_ERROR "Native benchmark failed: ${status} ${output} ${error}")
  endif()
  string(JSON count LENGTH "${output}" results)
  string(JSON version GET "${output}" schema_version)
  string(JSON batch GET "${output}" batch_steps)
  if(NOT count EQUAL 2 OR NOT version EQUAL 1 OR NOT batch EQUAL 8)
    message(FATAL_ERROR "Incorrect report schema: ${output}")
  endif()
  foreach(i RANGE 0 1)
    string(JSON row GET "${output}" results ${i})
    foreach(key status steps seconds ms_per_step steps_per_second)
      string(JSON ${key} GET "${row}" ${key})
    endforeach()
    math(EXPR remainder "${steps} % 8")
    if(NOT status STREQUAL "ok" OR steps LESS 8 OR NOT remainder EQUAL 0
        OR seconds LESS .03 OR NOT ms_per_step GREATER 0 OR NOT steps_per_second GREATER 0)
      message(FATAL_ERROR "Invalid completed timing: ${row}")
    endif()
    string(JSON native GET "${row}" native_report)
    string(JSON initial GET "${native}" initial_stats steps)
    string(JSON final GET "${native}" stats steps)
    string(JSON gpu GET "${native}" gpu_seconds)
    string(JSON finite GET "${native}" stats is_finite)
    string(JSON k0 GET "${native}" config k0)
    string(JSON integrator GET "${native}" config integrator)
    string(JSON form GET "${native}" config operator)
    string(JSON re GET "${native}" config reynolds)
    if(NOT initial EQUAL 0 OR NOT final EQUAL steps OR NOT gpu GREATER 0 OR NOT finite
        OR NOT k0 EQUAL 10 OR NOT integrator STREQUAL "etdrk4" OR NOT form STREQUAL "stress"
        OR NOT re EQUAL 27898.36614974335)
      message(FATAL_ERROR "Workload/reset contract changed: ${native}")
    endif()
    foreach(key simulation_time dt energy enstrophy palinstrophy)
      string(JSON value GET "${native}" initial_stats ${key})
      if(warmup EQUAL 0)
        set(initial_${i}_${key} "${value}")
      elseif(NOT value STREQUAL "${initial_${i}_${key}}")
        message(FATAL_ERROR "Warmup was not reset: ${i} ${key}: ${value} != ${initial_${i}_${key}}")
      endif()
    endforeach()
  endforeach()
endforeach()
# On cards smaller than the largest case's buffer lower bound, verify a memory
# skip is followed by a real successful case. Never allocate that large case on
# machines where the preflight would allow it.
string(JSON available GET "${output}" available_memory_bytes)
math(EXPR minimum "84 * 16384 * 16384 + 8 * 24576 * 24576")
if(minimum GREATER available)
  execute_process(COMMAND ${run} --sizes 16384 32 --seconds .01 --json
    RESULT_VARIABLE status OUTPUT_VARIABLE output ERROR_VARIABLE error)
  string(JSON first GET "${output}" results 0 status)
  string(JSON second GET "${output}" results 1 status)
  string(JSON reason GET "${output}" results 0 reason)
  if(NOT status EQUAL 1 OR NOT first STREQUAL "skipped" OR NOT second STREQUAL "ok"
      OR NOT reason MATCHES "insufficient device memory")
    message(FATAL_ERROR "Memory skip did not preserve the next case: ${output} ${error}")
  endif()
endif()
execute_process(COMMAND ${run} --sizes 32 --seconds .001 --device 2147483647 --json
  RESULT_VARIABLE status OUTPUT_VARIABLE output ERROR_VARIABLE error)
string(JSON reason GET "${output}" results 0 reason)
if(NOT status EQUAL 1 OR NOT reason MATCHES "CUDA device index out of range")
  message(FATAL_ERROR "Invalid device did not fail clearly: ${output} ${error}")
endif()
execute_process(COMMAND ${run} --sizes=32 --seconds=.01 --warmup=0
  RESULT_VARIABLE status OUTPUT_VARIABLE output ERROR_VARIABLE error)
if(NOT status EQUAL 0 OR NOT output MATCHES "FastTurbo 2D Benchmark"
    OR NOT output MATCHES "32²" OR NOT output MATCHES "48²" OR NOT output MATCHES "ms/step")
  message(FATAL_ERROR "Text report failed: ${output} ${error}")
endif()
