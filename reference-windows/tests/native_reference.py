"""Independent Float32 transcription of Swift's seeded velocity initializer.

This checks source-defined semantics, not a captured Metal endpoint. CUDA never
produces the expected array. Each row computes SplitMix64 counters directly.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
root=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(root/'windows/.python'))
import numpy as np

def native(n,k0,seed):
    half=n//2
    state=np.zeros((n,half+1),np.complex64)
    counter=0
    k0=np.float32(k0)
    normal=np.float32(np.pi)*k0*k0
    with np.errstate(over='ignore',under='ignore'):
        for iy in range(n):
            ky=iy if iy<=half else iy-n
            if abs(ky)>=half: continue
            x=np.arange(0 if ky>0 else 1,half,dtype=np.int64)
            indices=np.arange(counter+2,counter+2+x.size,dtype=np.uint64)
            counter+=x.size
            v=np.uint64(seed)+indices*np.uint64(0x9e3779b97f4a7c15)
            v=(v^(v>>np.uint64(30)))*np.uint64(0xbf58476d1ce4e5b9)
            v=(v^(v>>np.uint64(27)))*np.uint64(0x94d049bb133111eb)
            v^=v>>np.uint64(31)
            uniform=(v>>np.uint64(40)).astype(np.float32)/np.float32(1<<24)
            phase=np.float32(2)*np.float32(np.pi)*uniform
            amplitude=np.exp(-(x*x+ky*ky).astype(np.float32)/(k0*k0))/normal
            direction=np.cos(phase).astype(np.complex64)+np.complex64(1j)*np.sin(phase)
            nonzero=x!=0
            ratio=np.zeros(x.size,np.float32)
            np.divide(np.float32(ky),x.astype(np.float32),out=ratio,where=nonzero)
            velocity_y=direction*np.sqrt(amplitude/(np.float32(1)+ratio*ratio))
            velocity_x=velocity_y*(-ratio)
            velocity_x[~nonzero]=direction[~nonzero]*np.sqrt(amplitude[~nonzero])
            velocity_y[~nonzero]=0
            curl=velocity_y*x.astype(np.float32)-velocity_x*np.float32(ky)
            w=(np.complex64(1j)*curl)*np.float32(n*n)
            state[iy,x]=w
            if ky>0: state[(n-iy)%n,0]=np.conj(w[0])
    full=np.zeros((n,n),np.complex64)
    full[:,:half+1]=state/np.float32(n*n)
    full[:,half+1:]=np.conj(full[(-np.arange(n))%n,half-1:0:-1])
    return full

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--app',type=Path,default=root/'windows/build/2DFastTurbo.exe')
    p.add_argument('--output',type=Path,default=root/'windows/artifacts/native')
    p.add_argument('--resolutions',type=int,nargs='+',default=[128,256,512,1024,2048,4096])
    a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True);checks=[]
    for n in a.resolutions:
        spec=a.output/f'n{n}.c64';report=a.output/f'n{n}.json'
        subprocess.run([str(a.app.resolve()),'--benchmark','--resolution',str(n),'--steps','0','--warmup','0','--spectrum',str(spec),'--output',str(report)],check=True,capture_output=True)
        actual=np.fromfile(spec,dtype='<c8').reshape(n,n)
        expected=native(n,4,2)
        error=float(np.linalg.norm(actual-expected)/np.linalg.norm(expected))
        assert error<2e-6,(n,error)
        record=json.loads(report.read_text())
        # GPU launch / VkFFT size coverage includes one completed nonlinear step.
        proc=subprocess.run([str(a.app.resolve()),'--benchmark','--resolution',str(n),'--steps','1','--warmup','0'],check=True,capture_output=True,text=True)
        step=json.loads(proc.stdout)
        assert step['stats']['steps']==1 and step['stats']['simulation_time']>0
        checks.append({'n':n,'m':3*n//2,'native_relative_l2':error,'tolerance':2e-6,'initial_energy':record['stats']['energy'],'initial_enstrophy':record['stats']['enstrophy'],'one_step_passed':True})
        print(f'PASS native initializer and nonlinear step N={n} M={3*n//2}: L2={error:.3g}',flush=True)
    result={'passed':True,'reference':'Independent transcription of native Swift Float32/SplitMix64 initialization; not Metal captures','checks':checks}
    (a.output/'summary.json').write_text(json.dumps(result,indent=2)+'\n')
    return 0
if __name__=='__main__':raise SystemExit(main())
