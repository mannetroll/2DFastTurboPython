"""Compare exported GPU pixels with the macOS shader's equations in NumPy."""
import argparse
import json
from pathlib import Path
import struct
import sys
import zlib
root=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(root/'windows/.python'))
import numpy as np

def read_png(path):
    data=path.read_bytes();assert data[:8]==b'\x89PNG\r\n\x1a\n'
    pos=8;compressed=b''
    while pos<len(data):
        length,kind=struct.unpack('>I4s',data[pos:pos+8]);chunk=data[pos+8:pos+8+length];pos+=12+length
        if kind==b'IHDR':w,h,depth,color,_,_,interlace=struct.unpack('>IIBBBBB',chunk)
        if kind==b'IDAT':compressed+=chunk
    assert depth==8 and color in (2,6) and interlace==0
    bpp=3 if color==2 else 4;stride=w*bpp;raw=zlib.decompress(compressed);previous=bytearray(stride);rows=[]
    for y in range(h):
        start=y*(stride+1);kind=raw[start];row=bytearray(raw[start+1:start+1+stride])
        for x in range(stride):
            left=row[x-bpp] if x>=bpp else 0;up=previous[x];corner=previous[x-bpp] if x>=bpp else 0
            if kind==0:predictor=0
            elif kind==1:predictor=left
            elif kind==2:predictor=up
            elif kind==3:predictor=(left+up)//2
            else:
                assert kind==4
                p=left+up-corner;a=abs(p-left);b=abs(p-up);c=abs(p-corner)
                predictor=left if a<=b and a<=c else up if b<=c else corner
            row[x]=(row[x]+predictor)&255
        rows.append(bytes(row));previous=row
    return np.frombuffer(b''.join(rows),np.uint8).reshape(h,w,bpp)[:,:,:3]

def smooth(a,b,x):
    t=np.clip((x-a)/(b-a),0,1)
    return t*t*(3-2*t)
def mix(a,b,t):return np.asarray(a)*(1-t[...,None])+np.asarray(b)*t[...,None]
def palette(value,index,signed):
    if index==3:
        anchors=np.array([[.001,0,.014],[.258,.039,.406],[.578,.148,.404],[.865,.317,.226],[.988,.645,.040],[.988,.998,.645]])
        t=(.5+.5*np.clip(value,-1,1)) if signed else value
        p=np.clip(t,0,1)*5;i=np.minimum(p.astype(int),4)
        return mix(anchors[i],anchors[i+1],p-i)
    if signed:
        mids=[[[.48,.20,.79],[.025,.60,.58]],[[.28,.17,.69],[.92,.19,.065]],[[.14,.24,.68],[.01,.46,.78]]]
        highs=[[[.94,.67,1],[.48,1,.84]],[[.76,.64,1],[1,.88,.43]],[[.70,.79,1],[.70,1,.99]]]
        positive=(value>=0).astype(int);mid=np.array(mids[index])[positive];high=np.array(highs[index])[positive];amount=np.clip(abs(value),0,1)
        return mix(mix([.017,.026,.055],mid,smooth(0,.60,amount)),high,smooth(.34,1,amount))
    mid=[[.30,.17,.63],[.67,.10,.17],[.02,.33,.72]][index]
    high=[[.36,1,.80],[1,.86,.41],[.73,.97,1]][index];amount=np.clip(value,0,1)
    return mix(mix([.019,.028,.063],mid,smooth(0,.50,amount)),high,smooth(.32,1,amount))

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--directory',type=Path,default=root/'windows/artifacts/colors');p.add_argument('--n',type=int,default=32);a=p.parse_args()
    fields=np.fromfile(a.directory/'fields.f32',dtype='<f4').reshape(3,a.n,a.n).astype(np.float64)
    manifest=json.loads((a.directory/'manifest.json').read_text());checks=[]
    for case in manifest:
        actual=read_png(a.directory/case['file']);h,w,_=actual.shape
        # Metal/HLSL y increases upwards; sample at pixel centers, with periodic interpolation.
        x,y=np.meshgrid((np.arange(w)+.5)/w*a.n,(1-(np.arange(h)+.5)/h)*a.n)
        ix=np.floor(x).astype(int)%a.n;iy=np.floor(y).astype(int)%a.n;fx=x-np.floor(x);fy=y-np.floor(y)
        planes=fields[:,iy,ix]*(1-fx)*(1-fy)+fields[:,iy,(ix+1)%a.n]*fx*(1-fy)+fields[:,(iy+1)%a.n,ix]*(1-fx)*fy+fields[:,(iy+1)%a.n,(ix+1)%a.n]*fx*fy
        omega=planes[0];maximum=max(abs(fields[0]).max(),.0001);speedmax=max(np.hypot(fields[1],fields[2]).max(),.0001)
        if case['field']==0:
            if case['range']==0:value=np.clip((omega-fields[0].mean())/max(case['sigma']*fields[0].std(),1e-12),-1,1)
            else:value=np.tanh(omega/maximum*case['exposure']*2.4)
        elif case['field']==1:value=1-np.exp(-np.hypot(planes[1],planes[2])/speedmax*case['exposure']*2.1)
        else:value=1-np.exp(-(omega/maximum)**2*case['exposure']*8)
        expected=np.rint(palette(value,case['palette'],case['field']==0)*255).astype(np.int16)
        difference=np.abs(actual.astype(np.int16)-expected);maximum_error=int(difference.max())
        assert maximum_error<=2,(case,maximum_error)
        checks.append({'file':case['file'],'maximum_byte_error':maximum_error,'mean_byte_error':float(difference.mean())})
    result={'passed':True,'reference':'Independent NumPy evaluation of TurboRendering.metal; SDR pixels including orientation/wrap','tolerance_bytes':2,'checks':checks}
    (a.directory/'validation.json').write_text(json.dumps(result,indent=2)+'\n')
    print(f'PASS all {len(checks)} GPU palette/field/range combinations; max byte error {max(c["maximum_byte_error"] for c in checks)}')
if __name__=='__main__':main()
