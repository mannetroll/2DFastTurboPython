// Direct HLSL port of TurboRendering.metal. Palettes and mapping equations are unchanged.
ByteAddressBuffer field : register(t0);
cbuffer Uniforms : register(b0) {
    uint gridSize, palette, display, showFlowLines;
    float exposure, vorticityScale, speedScale, vorticityMean;
    uint vorticityColorRange, outputHDR;
    float canvasPixels, rounded;
};
float3 outputColor(float3 srgb) {
    if (!outputHDR) return srgb;
    float3 linearRGB = float3(srgb.x<=.04045?srgb.x/12.92:pow((srgb.x+.055)/1.055,2.4),
                          srgb.y<=.04045?srgb.y/12.92:pow((srgb.y+.055)/1.055,2.4),
                          srgb.z<=.04045?srgb.z/12.92:pow((srgb.z+.055)/1.055,2.4));
    float peak=max(linearRGB.x,max(linearRGB.y,linearRGB.z));
    return linearRGB*(1+smoothstep(.25,1,peak));
}
struct FieldVertex { float4 position:SV_Position; float2 uv:TEXCOORD0; };
FieldVertex fieldVertex(uint id:SV_VertexID) {
    float2 p=float2((id<<1)&2,id&2);
    FieldVertex o;o.position=float4(p*2-1,0,1);o.uv=p;return o;
}
float samplePlane(float2 uv,uint plane) {
    float2 p=frac(uv)*float(gridSize);uint2 a=(uint2)floor(p);uint2 b=(a+1)%gridSize;
    float2 f=frac(p);uint offset=plane*gridSize*gridSize;
    float lo=lerp(asfloat(field.Load(4*(offset+a.y*gridSize+a.x))),asfloat(field.Load(4*(offset+a.y*gridSize+b.x))),f.x);
    float hi=lerp(asfloat(field.Load(4*(offset+b.y*gridSize+a.x))),asfloat(field.Load(4*(offset+b.y*gridSize+b.x))),f.x);
    return lerp(lo,hi,f.y);
}
float2 sampleVelocity(float2 uv){return float2(samplePlane(uv,1),samplePlane(uv,2));}
float3 infernoColor(float t) {
    const float3 colors[6]={float3(.001,.000,.014),float3(.258,.039,.406),float3(.578,.148,.404),float3(.865,.317,.226),float3(.988,.645,.040),float3(.988,.998,.645)};
    float p=saturate(t)*5;uint i=min((uint)p,4u);return lerp(colors[i],colors[i+1],p-float(i));
}
float3 signedColor(float value) {
    if(palette==3)return infernoColor(.5+.5*clamp(value,-1,1));
    float amount=saturate(abs(value));bool positive=value>=0;
    float3 base=float3(.017,.026,.055),middle,bright;
    if(palette==1){middle=positive?float3(.92,.19,.065):float3(.28,.17,.69);bright=positive?float3(1,.88,.43):float3(.76,.64,1);}
    else if(palette==2){middle=positive?float3(.01,.46,.78):float3(.14,.24,.68);bright=positive?float3(.70,1,.99):float3(.70,.79,1);}
    else {middle=positive?float3(.025,.60,.58):float3(.48,.20,.79);bright=positive?float3(.48,1,.84):float3(.94,.67,1);}
    float3 color=lerp(base,middle,smoothstep(0,.60,amount));return lerp(color,bright,smoothstep(.34,1,amount));
}
float3 speedColor(float value) {
    if(palette==3)return infernoColor(value);
    float amount=saturate(value);float3 low=float3(.019,.028,.063);
    float3 mid=palette==1?float3(.67,.10,.17):palette==2?float3(.02,.33,.72):float3(.30,.17,.63);
    float3 high=palette==1?float3(1,.86,.41):palette==2?float3(.73,.97,1):float3(.36,1,.80);
    return lerp(lerp(low,mid,smoothstep(0,.50,amount)),high,smoothstep(.32,1,amount));
}
float4 fieldPixel(FieldVertex input):SV_Target {
    if(rounded>0){float2 q=float2(min(input.uv.x,1-input.uv.x),input.uv.y)*canvasPixels;
        if(q.x<rounded&&q.y<rounded&&length(q-rounded)>rounded)discard;}
    float3 color;
    if(display==0){float omega=samplePlane(input.uv,0);
        float value=vorticityColorRange==0?clamp((omega-vorticityMean)/vorticityScale,-1,1):tanh(omega/vorticityScale*max(exposure,.02)*2.4);
        color=signedColor(value);
    }else if(display==1){float speed=length(sampleVelocity(input.uv));float value=1-exp(-speed/speedScale*max(exposure,.02)*2.1);color=speedColor(value);}
    else {float omega=samplePlane(input.uv,0)/vorticityScale;float value=1-exp(-omega*omega*max(exposure,.02)*8);color=speedColor(value);}
    return float4(outputColor(color),1);
}
struct FlowVertex {float4 position:SV_Position;float opacity:TEXCOORD0;};
float seedHash(float2 value){return frac(sin(dot(value,float2(127.1,311.7)))*43758.5453);}
FlowVertex flowVertex(uint id:SV_VertexID) {
    const uint segments=18;uint seed=id/(segments*2);uint endpoint=(id%(segments*2))/2+(id&1);
    float2 cell=float2(seed%32,seed/32),jitter=float2(seedHash(cell),seedHash(cell+39.4));
    float2 origin=(cell+.25+.5*jitter)/32,p=origin;
    int steps=int(endpoint)-9;float dt=(steps<0?-1:1)*.010/max(speedScale,.0001);
    for(int i=0;i<abs(steps);++i){float2 v0=sampleVelocity(p);float2 midpoint=p+.5*dt*v0;p+=dt*sampleVelocity(midpoint);}
    float speed=length(sampleVelocity(p))/max(speedScale,.0001);
    float taper=pow(max(sin(float(endpoint)/float(segments)*3.14159265358979323846),0),.7);
    FlowVertex o;o.position=float4(p*2-1,0,1);o.opacity=taper*smoothstep(.005,.15,speed)*.25;return o;
}
float4 flowPixel(FlowVertex input):SV_Target{return float4(outputColor(float3(.76,.93,1)),input.opacity);}
