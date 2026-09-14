// Build-time only: the application loads these exact bytecodes from PE resources.
#include <windows.h>
#include <d3dcompiler.h>
#include <wrl/client.h>
#include <filesystem>
#include <fstream>
#include <iostream>
int wmain(int argc, wchar_t** argv) {
    if(argc!=3)return 2;
    const char* names[]={"fieldVertex","fieldPixel","flowVertex","flowPixel"};
    for(int i=0;i<4;++i){
        Microsoft::WRL::ComPtr<ID3DBlob> code,error;
        HRESULT result=D3DCompileFromFile(argv[1],nullptr,D3D_COMPILE_STANDARD_FILE_INCLUDE,names[i],i%2?"ps_5_0":"vs_5_0",
            D3DCOMPILE_OPTIMIZATION_LEVEL3|D3DCOMPILE_IEEE_STRICTNESS,0,&code,&error);
        if(FAILED(result)){if(error)std::cerr.write(static_cast<char*>(error->GetBufferPointer()),error->GetBufferSize());return 2;}
        std::ofstream file(std::filesystem::path(argv[2])/(std::string(names[i])+".cso"),std::ios::binary);
        if(!file.write(static_cast<char*>(code->GetBufferPointer()),code->GetBufferSize()))return 2;
    }
    return 0;
}
