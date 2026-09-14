#pragma once
#include <cuda_runtime.h>
#include <stdexcept>
#include <string>
inline void cudaCheck(cudaError_t result, const char* operation) {
    if (result != cudaSuccess) throw std::runtime_error(std::string(operation)+": "+cudaGetErrorString(result));
}
#define CUDA_CHECK(call) cudaCheck((call), #call)
// Component arithmetic follows SIMD2/3<Float> in the authoritative Metal code.
struct V2 {
    float x, y;
    __host__ __device__ V2(float a=0):x(a),y(a) {}
    __host__ __device__ V2(float a,float b):x(a),y(b) {}
    __host__ __device__ V2 operator+(V2 b)const{return {x+b.x,y+b.y};}
    __host__ __device__ V2 operator-(V2 b)const{return {x-b.x,y-b.y};}
    __host__ __device__ V2 operator*(float b)const{return {x*b,y*b};}
    __host__ __device__ V2 operator/(float b)const{return {x/b,y/b};}
    __host__ __device__ V2& operator+=(V2 b){*this=*this+b;return *this;}
    __host__ __device__ V2& operator*=(float b){*this=*this*b;return *this;}
};
__host__ __device__ inline V2 operator*(float a,V2 b){return b*a;}
#ifdef __CUDACC__
struct V3 {
    float x,y,z;
    __device__ V3(float a,float b,float c):x(a),y(b),z(c){}
    __device__ V3& operator*=(V3 b){x*=b.x;y*=b.y;z*=b.z;return *this;}
    __device__ V3& operator+=(V3 b){x+=b.x;y+=b.y;z+=b.z;return *this;}
};
__device__ inline V3 operator/(float a,V3 b){return {a/b.x,a/b.y,a/b.z};}
__device__ inline bool finite(V2 a){return isfinite(a.x)&&isfinite(a.y);}
__device__ inline float dot(V2 a,V2 b){return a.x*b.x+a.y*b.y;}
__device__ inline float length(V2 a){return sqrtf(dot(a,a));}
#endif
