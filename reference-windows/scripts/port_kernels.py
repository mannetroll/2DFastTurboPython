"""Mechanical syntax translation; mathematical expressions remain from Metal.

Run when updating the reference, then review generated kernels.cuh and run all
numerical tests. No code is generated at application startup.
"""
from pathlib import Path
import re
root = Path(__file__).resolve().parents[2]
source = (root/'reference-macos/2DFastTurbo/Sources/FastTurboCore/FastTurboShaders.swift').read_text(encoding='utf-8')
source = source.split('static let source = #"""')[1].split('"""#')[0]
source = '\n'.join(line[4:] if line.startswith('    ') else line for line in source.splitlines())
source = source[source.index('struct Params'):]
source = re.sub(r'\[\[buffer\(\d+\)\]\]', '', source)
source = source.replace('atomic_uint','uint').replace('ulong steps','unsigned long long steps')
source = source.replace('constant Params &p','Params p')
source = source.replace('paddedComplexPitch()', 'paddedComplexPitch(p)')
source = source.replace('uint paddedComplexPitch(p)', 'uint paddedComplexPitch(Params p)')
for a,b in [('specializedN','p.n'),('specializedM','p.m'),('specializedIntegrator','p.integrator'),('specializedNonlinearFormulation','p.nonlinearFormulation')]:
    source=source.replace(a,b)
source = source.replace('device ClockState &clock', 'ClockState *state')
source = source.replace('device ', '').replace('threadgroup ', '__shared__ ')
source = source.replace('threadgroup_barrier(mem_flags::mem_threadgroup)', '__syncthreads()')
source = source.replace('atomic_load_explicit(&clock.valid, memory_order_relaxed)', 'clock.valid')
source = source.replace('atomic_store_explicit(&clock.valid, 0u, memory_order_relaxed)', 'atomicExch(&clock.valid, 0u)')
source = source.replace('all(isfinite(omega[index]))','finite(omega[index])').replace('all(isfinite(w))','finite(w)')
source = source.replace('float2', 'V2').replace('float3', 'V3').replace('float4(', 'make_float4(')
source = re.sub(r'(?<!\w)exp\(', 'expf(',source)
source = re.sub(r'(?<!\w)abs\((z|u|v|w)\)', r'fabsf(\1)',source)
source = source.replace('inline ', '__device__ inline ')
def kernel(match):
    name, args = match.group(1),match.group(2)
    injected=[]
    if '[[thread_position_in_grid]]' in args:
        if 'uint2 gid' in args: injected.append('uint2 gid = make_uint2(blockIdx.x*blockDim.x+threadIdx.x, blockIdx.y*blockDim.y+threadIdx.y);')
        else: injected.append('uint tid = blockIdx.x*blockDim.x+threadIdx.x;')
    if '[[thread_index_in_threadgroup]]' in args: injected.append('uint local = threadIdx.x;')
    if '[[threadgroup_position_in_grid]]' in args: injected.append('uint group = blockIdx.x;')
    if 'ClockState *state' in args: injected.append('ClockState &clock = *state;')
    args = re.sub(r',\s*uint2?\s+\w+\s*\[\[\w+\]\]', '',args)
    return '__global__ void '+name+'('+args.strip()+') {\n    '+'\n    '.join(injected)
source = re.sub(r'kernel void (\w+)\((.*?)\)\s*\{',kernel,source,flags=re.S)
header = '// Derived from FastTurboShaders.swift. CUDA syntax port; see scripts/port_kernels.py.\n#pragma once\n#include "cuda_support.cuh"\nusing uint = unsigned int;\n'
(root/'windows/src/kernels.cuh').write_text(header+source+'\n',encoding='utf-8')
