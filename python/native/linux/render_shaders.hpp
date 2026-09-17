#pragma once
// Direct GLSL port of reference-windows/src/render.hlsl (TurboRendering.metal).
// Preserve interpolation, periodic orientation, palettes and streamline equations.
namespace turbo {
inline constexpr auto shaderCommon = R"GLSL(
uniform samplerBuffer field;
uniform int gridSize, palette, display, vorticityColorRange;
uniform float exposure, vorticityScale, speedScale, vorticityMean;
uniform float rounded, canvasPixels;
float samplePlane(vec2 uv, int plane) {
    vec2 p=fract(uv)*float(gridSize); ivec2 a=ivec2(floor(p)), b=(a+1)%gridSize;
    vec2 f=fract(p); int offset=plane*gridSize*gridSize;
    float lo=mix(texelFetch(field,offset+a.y*gridSize+a.x).x,texelFetch(field,offset+a.y*gridSize+b.x).x,f.x);
    float hi=mix(texelFetch(field,offset+b.y*gridSize+a.x).x,texelFetch(field,offset+b.y*gridSize+b.x).x,f.x);
    return mix(lo,hi,f.y);
}
vec2 sampleVelocity(vec2 uv) { return vec2(samplePlane(uv,1),samplePlane(uv,2)); }
vec3 infernoColor(float t) {
    const vec3 colors[6]=vec3[6](vec3(.001,.000,.014),vec3(.258,.039,.406),vec3(.578,.148,.404),vec3(.865,.317,.226),vec3(.988,.645,.040),vec3(.988,.998,.645));
    float p=clamp(t,0.,1.)*5.; int i=min(int(p),4); return mix(colors[i],colors[i+1],p-float(i));
}
vec3 signedColor(float value) {
    if(palette==3)return infernoColor(.5+.5*clamp(value,-1.,1.));
    float amount=clamp(abs(value),0.,1.); bool positive=value>=0.;
    vec3 base=vec3(.017,.026,.055), middle, bright;
    if(palette==1){middle=positive?vec3(.92,.19,.065):vec3(.28,.17,.69);bright=positive?vec3(1,.88,.43):vec3(.76,.64,1);}
    else if(palette==2){middle=positive?vec3(.01,.46,.78):vec3(.14,.24,.68);bright=positive?vec3(.70,1,.99):vec3(.70,.79,1);}
    else {middle=positive?vec3(.025,.60,.58):vec3(.48,.20,.79);bright=positive?vec3(.48,1,.84):vec3(.94,.67,1);}
    return mix(mix(base,middle,smoothstep(0.,.60,amount)),bright,smoothstep(.34,1.,amount));
}
vec3 speedColor(float value) {
    if(palette==3)return infernoColor(value);
    float amount=clamp(value,0.,1.);vec3 low=vec3(.019,.028,.063);
    vec3 mid=palette==1?vec3(.67,.10,.17):palette==2?vec3(.02,.33,.72):vec3(.30,.17,.63);
    vec3 high=palette==1?vec3(1,.86,.41):palette==2?vec3(.73,.97,1):vec3(.36,1,.80);
    return mix(mix(low,mid,smoothstep(0.,.50,amount)),high,smoothstep(.32,1.,amount));
}
)GLSL";
inline constexpr auto fieldVertex = R"GLSL(
out vec2 uv;
void main(){vec2 p=vec2((gl_VertexID<<1)&2,gl_VertexID&2);gl_Position=vec4(p*2.-1.,0,1);uv=p;}
)GLSL";
inline constexpr auto fieldPixel = R"GLSL(
in vec2 uv; out vec4 color;
void main(){
    if(rounded>0.){
        vec2 q=vec2(min(uv.x,1.-uv.x),uv.y)*canvasPixels;
        if(q.x<rounded&&q.y<rounded&&length(q-vec2(rounded))>rounded)discard;
    }
    vec3 c;
    if(display==0){float omega=samplePlane(uv,0);
        float value=vorticityColorRange==0?clamp((omega-vorticityMean)/vorticityScale,-1.,1.):tanh(omega/vorticityScale*max(exposure,.02)*2.4);
        c=signedColor(value);
    }else if(display==1){float value=1.-exp(-length(sampleVelocity(uv))/speedScale*max(exposure,.02)*2.1);c=speedColor(value);}
    else {float omega=samplePlane(uv,0)/vorticityScale;c=speedColor(1.-exp(-omega*omega*max(exposure,.02)*8.));}
    color=vec4(c,1);
}
)GLSL";
inline constexpr auto flowVertex = R"GLSL(
out float opacity;
float seedHash(vec2 value){return fract(sin(dot(value,vec2(127.1,311.7)))*43758.5453);}
void main(){
    int id=gl_VertexID, segments=18, seed=id/(segments*2), endpoint=(id%(segments*2))/2+(id&1);
    vec2 cell=vec2(seed%32,seed/32),jitter=vec2(seedHash(cell),seedHash(cell+39.4));
    vec2 p=(cell+.25+.5*jitter)/32.;
    int steps=endpoint-9;float dt=(steps<0?-1.:1.)*.010/max(speedScale,.0001);
    for(int i=0;i<abs(steps);++i){vec2 v0=sampleVelocity(p);vec2 midpoint=p+.5*dt*v0;p+=dt*sampleVelocity(midpoint);}
    float speed=length(sampleVelocity(p))/max(speedScale,.0001);
    float taper=pow(max(sin(float(endpoint)/float(segments)*3.14159265358979323846),0.),.7);
    gl_Position=vec4(p*2.-1.,0,1);opacity=taper*smoothstep(.005,.15,speed)*.25;
}
)GLSL";
inline constexpr auto flowPixel = R"GLSL(
in float opacity; out vec4 color;
void main(){color=vec4(.76,.93,1,opacity);}
)GLSL";
}
