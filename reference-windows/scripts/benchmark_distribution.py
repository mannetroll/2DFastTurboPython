"""Compare preserved original and packaged VkFFT binaries, with interleaved runs."""
import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import statistics
import subprocess
from audit_runtime import clean_environment

root=Path(__file__).resolve().parents[2]

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runs',type=int,default=3)
    parser.add_argument('--seconds',type=float,default=5)
    args=parser.parse_args()
    if args.runs<1 or args.seconds<=0: parser.error('Positive run count and duration required')
    output=root/'windows/artifacts/packaging/performance';output.mkdir(parents=True,exist_ok=True)
    binaries={'original':root/'windows/build-packaging-baseline/2DFastTurbo.exe',
              'packaged':root/'windows/dist/2DFastTurbo.exe'}
    hashes={name:hashlib.sha256(path.read_bytes()).hexdigest() for name,path in binaries.items()}
    records=[]
    for run in range(1,args.runs+1):
        for mode in ('headless','gui'):
            for variant in (('original','packaged') if run%2 else ('packaged','original')):
                name=f'{mode}-{variant}-{run}';app=binaries[variant]
                command=[str(app),'--benchmark' if mode=='headless' else '--gui-benchmark',
                         '--resolution','1024','--reynolds','50000','--seed','2','--k0','4',
                         '--integrator','etdrk4','--operator','stress','--cfl','3',
                         '--wall-seconds',str(args.seconds),'--output',str(output/f'{name}.json')]
                command+=['--warmup','8','--batch','8'] if mode=='headless' else ['--gui-warmup-seconds','2','--canvas-size','640']
                env=clean_environment() if variant=='packaged' else os.environ.copy()
                if variant=='original':env['PATH']='C:/Program Files/NVIDIA GPU Computing Toolkit/CUDA/v13.0/bin/x64;'+env['PATH']
                with (output/f'{name}.log').open('w') as log:
                    subprocess.run(command,env=env,cwd=app.parent,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=180)
                result=json.loads((output/f'{name}.json').read_text());steps=result['stats']['steps']
                records.append({'variant':variant,'mode':mode,'run':run,'R_turbo':result['simtime_per_s'],
                                'ms_per_step':1000*result['seconds']/steps,'gpu_ms_per_step':1000*result['gpu_seconds']/steps,
                                'steps_per_s':result['steps_per_s'],'presented_fps':result['presented_fps'],
                                'executable_sha256':hashes[variant]})
                print(f'{name}: R_turbo={records[-1]["R_turbo"]:.6f}, {records[-1]["ms_per_step"]:.4f} ms/step',flush=True)
    with (output/'runs.csv').open('w',newline='') as file:
        writer=csv.DictWriter(file,fieldnames=list(records[0]));writer.writeheader();writer.writerows(records)
    summaries=[]
    for mode in ('headless','gui'):
        for variant in binaries:
            selected=[r for r in records if r['mode']==mode and r['variant']==variant]
            summaries.append({'mode':mode,'variant':variant,**{key:statistics.mean(r[key] for r in selected) for key in ('R_turbo','ms_per_step','gpu_ms_per_step','presented_fps')},
                              'minimum_R_turbo':min(r['R_turbo'] for r in selected),'maximum_R_turbo':max(r['R_turbo'] for r in selected)})
    result={'runs':records,'means':summaries,'n':1024,'m':1536,'seed':2,'k0':4,'reynolds':50000,'cfl':3,'integrator':'etdrk4','operator':'stress'}
    (output/'summary.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(summaries,indent=2))

if __name__=='__main__':main()
