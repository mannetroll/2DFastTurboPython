"""Capture identical portable fixtures with either the macOS or Windows CLI.

Use this script on each machine, then compare_captures.py. It records the real
host platform and executable hash; a Windows run is never labelled as Metal.
"""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import sys
root=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(root/'windows/.python'))
sys.path.insert(0,str(root/'reference-macos/2DFastTurbo/Scripts'))
from validate import cosine_fixture, run_solver
import numpy as np

def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--app',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();app=a.app.resolve();a.output.mkdir(parents=True,exist_ok=True)
    cases=[]
    for n in (32,34,128):
        for high in (False,True):
            field=cosine_fixture(n,high=high).astype('<f4')
            for method in ('imexRK3','ifHeunRK3','etdrk4'):
                for form in ('stress','advective'):
                    name=f'n{n}-high{int(high)}-{method}-{form}'
                    directory=a.output/name
                    run=run_solver(app,field,directory,method=method,operator_name=form,dt=.001,until=.02,reynolds=50000)
                    cases.append({'case':name,'n':n,'method':method,'form':form,'initial_sha256':digest(directory/'initial.f32'),
                                  'spectrum_sha256':digest(directory/'spectrum.c64'),'spectrum':f'{name}/spectrum.c64',
                                  'time':run.simulation_time,'steps':run.steps})
    manifest={'schema':1,'platform':sys.platform,'os':platform.platform(),'executable_sha256':digest(app),
              'reference_sources':{str(path.relative_to(root)):digest(path) for path in (root/'reference-macos/2DFastTurbo/Sources').rglob('*') if path.is_file()},'cases':cases}
    (a.output/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(f'Captured {len(cases)} cases on {sys.platform}: {a.output}')
if __name__=='__main__':main()
