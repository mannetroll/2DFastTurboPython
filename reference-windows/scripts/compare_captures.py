"""Compare captured macOS Metal endpoints against Windows CUDA endpoints."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
root=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(root/'windows/.python'))
sys.path.insert(0,str(root/'reference-macos/2DFastTurbo/Scripts'))
from validate import FourierGrid
import numpy as np

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--macos',type=Path,required=True)
    p.add_argument('--windows',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--tolerance',type=float,default=3e-5)
    a=p.parse_args()
    mac=json.loads((a.macos/'manifest.json').read_text());win=json.loads((a.windows/'manifest.json').read_text())
    if mac['platform']!='darwin' or win['platform']!='win32':raise ValueError('Requires real Darwin and Windows captures.')
    if mac['reference_sources']!=win['reference_sources']:raise ValueError('Reference source revisions differ.')
    mc={c['case']:c for c in mac['cases']};wc={c['case']:c for c in win['cases']}
    if mc.keys()!=wc.keys() or not mc:raise ValueError('Capture case sets differ or are empty.')
    rows=[]
    for name,m in mc.items():
        w=wc[name]
        for key in ('n','method','form','initial_sha256','steps'):
            if m[key]!=w[key]:raise ValueError(f'{name}: mismatched {key}')
        if abs(m['time']-w['time'])>1e-12:raise ValueError(f'{name}: endpoints differ')
        arrays=[]
        for directory,case in ((a.macos,m),(a.windows,w)):
            path=directory/case['spectrum'];data=path.read_bytes()
            if hashlib.sha256(data).hexdigest()!=case['spectrum_sha256']:raise ValueError(f'{name}: capture hash mismatch')
            arr=np.frombuffer(data,dtype='<c8').reshape(m['n'],m['n']).astype(np.complex128)
            if not np.isfinite(arr).all():raise ValueError(f'{name}: nonfinite capture')
            arrays.append(arr)
        errors=FourierGrid(m['n']).error(arrays[1],arrays[0]);passed=max(errors.values())<=a.tolerance
        rows.append({'case':name,'passed':passed,'relative_errors':errors})
    result={'passed':all(c['passed'] for c in rows),'tolerance':a.tolerance,'comparison':'captured Metal vs CUDA','cases':rows}
    a.output.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
    return 0 if result['passed'] else 1
if __name__=='__main__':raise SystemExit(main())
