#pragma once
#include "solver.hpp"
#include <windows.h>
#include <d2d1_1.h>
#include <d2d1_3.h>
#include <dwrite_1.h>
#include <d3d11.h>
#include <dxgi1_6.h>
#include <wrl/client.h>
#include <filesystem>
#include <map>
namespace turbo {
using Microsoft::WRL::ComPtr;
void hrCheck(HRESULT code,const char* operation);
class Renderer {
    HWND window;
    ComPtr<IDXGIFactory2> factory;
    ComPtr<ID3D11Device> device;
    ComPtr<ID3D11DeviceContext> context;
    ComPtr<IDXGISwapChain1> swap;
    ComPtr<ID3D11Texture2D> back;
    ComPtr<ID3D11RenderTargetView> target;
    ComPtr<ID2D1Factory1> d2dFactory;
    ComPtr<ID2D1Device> d2dDevice;
    ComPtr<ID2D1DeviceContext> d2d;
    ComPtr<ID2D1Bitmap1> bitmap;
    ComPtr<ID2D1DeviceContext5> iconContext;
    ComPtr<ID2D1SvgDocument> headerSymbol;
    ComPtr<ID2D1SolidColorBrush> brush;
    ComPtr<IDWriteFactory> write;
    ComPtr<IDWriteTextLayout1> titleLayout;
    ComPtr<IDWriteTextLayout> subtitleLayout;
    std::wstring subtitleValue;
    std::map<std::pair<int,bool>,ComPtr<IDWriteTextFormat>> fonts;
    ComPtr<ID3D11VertexShader> fieldVS,flowVS;
    ComPtr<ID3D11PixelShader> fieldPS,flowPS;
    ComPtr<ID3D11Buffer> uniforms,fields;
    ComPtr<ID3D11ShaderResourceView> fieldView;
    ComPtr<ID3D11RasterizerState> raster;
    ComPtr<ID3D11BlendState> blend;
    cudaGraphicsResource* interop=nullptr;
    cudaStream_t copyStream{};
    std::shared_ptr<Frame> uploaded;
    int fieldN=0;
    int cudaDevice=0;
    UINT width=0,height=0;
    bool hdr=false;
    HANDLE frameLatency=nullptr;
    void createTarget();
    void upload(const std::shared_ptr<Frame>& frame);
    void field(ID3D11RenderTargetView* target,const std::shared_ptr<Frame>& frame,const Display& display,
               float x,float y,float side,bool hdrOutput,float radius);
    void saveTexture(ID3D11Texture2D* texture,const std::filesystem::path& path);
public:
    explicit Renderer(HWND window);
    ~Renderer();
    int gpu()const{return cudaDevice;}
    bool isHDR()const{return hdr;}
    HANDLE frameSignal()const{return frameLatency;}
    size_t allocation()const{return size_t(fieldN)*fieldN*3*4+size_t(width)*height*(hdr?8:4)*2;}
    void resize(UINT width,UINT height);
    void begin(float dpiScale,bool clear);
    void end();
    void present();
    void drawField(const std::shared_ptr<Frame>& frame,const Display& display,float x,float y,float side,float scale);
    void rectangle(D2D1_RECT_F rect,D2D1_COLOR_F color,float radius=0,bool outline=false);
    void line(float x1,float y1,float x2,float y2,D2D1_COLOR_F color,float thickness=1);
    void circle(float x,float y,float radius,D2D1_COLOR_F color,bool outline=false);
    void headerIcon(float x,float y);
    void text(const std::wstring& text,D2D1_RECT_F rect,float size,D2D1_COLOR_F color,bool mono=false,bool right=false);
    void title(float x,float y,D2D1_COLOR_F color);
    void elidedText(const std::wstring& text,D2D1_RECT_F rect,float size,D2D1_COLOR_F color);
    void gradient(D2D1_RECT_F rect,int palette);
    void clip(D2D1_RECT_F rect);
    void unclip();
    void exportPNG(const std::shared_ptr<Frame>& frame,const Display& display,const std::filesystem::path& path,int side=1800);
    void capturePNG(const std::filesystem::path& path);
};
}
