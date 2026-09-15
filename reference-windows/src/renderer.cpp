#include "renderer.hpp"
#include "cuda_support.cuh"
#include <cuda_d3d11_interop.h>
#include "resources.hpp"
#include "desktop_resources.h"
#include <d2d1svg.h>
#include <wincodec.h>
#include <DirectXPackedVector.h>
#include <sstream>
#include <vector>

namespace turbo {
void hrCheck(HRESULT code,const char* operation){if(FAILED(code)){std::ostringstream s;s<<operation<<" failed (0x"<<std::hex<<uint32_t(code)<<")";throw std::runtime_error(s.str());}}
static D2D1_COLOR_F linearColor(D2D1_COLOR_F c,bool hdr){if(hdr){auto f=[](float x){return x<=.04045f?x/12.92f:std::pow((x+.055f)/1.055f,2.4f);};c.r=f(c.r);c.g=f(c.g);c.b=f(c.b);}return c;}
Renderer::Renderer(HWND h):window(h) {
    hrCheck(CreateDXGIFactory1(IID_PPV_ARGS(&factory)),"Create DXGI factory");
    ComPtr<IDXGIAdapter1> adapter;
    bool found=false;
    for(UINT i=0;factory->EnumAdapters1(i,adapter.ReleaseAndGetAddressOf())!=DXGI_ERROR_NOT_FOUND;++i) {
        if(cudaD3D11GetDevice(&cudaDevice,adapter.Get())==cudaSuccess){found=true;break;}
        cudaGetLastError();
    }
    if(!found)throw std::runtime_error("No CUDA-compatible Direct3D adapter found.");
    CUDA_CHECK(cudaSetDevice(cudaDevice));CUDA_CHECK(cudaStreamCreateWithFlags(&copyStream,cudaStreamNonBlocking));
    D3D_FEATURE_LEVEL level;
    hrCheck(D3D11CreateDevice(adapter.Get(),D3D_DRIVER_TYPE_UNKNOWN,nullptr,D3D11_CREATE_DEVICE_BGRA_SUPPORT,nullptr,0,D3D11_SDK_VERSION,&device,&level,&context),"Create D3D11 device");
    // scRGB extends the same palette on an active Windows HDR display.
    HMONITOR monitor=MonitorFromWindow(h,MONITOR_DEFAULTTONEAREST);
    for(UINT i=0;;++i){ComPtr<IDXGIOutput> output;if(adapter->EnumOutputs(i,&output)==DXGI_ERROR_NOT_FOUND)break;
        DXGI_OUTPUT_DESC desc{};output->GetDesc(&desc);
        if(desc.Monitor==monitor){ComPtr<IDXGIOutput6> modern;if(SUCCEEDED(output.As(&modern))){DXGI_OUTPUT_DESC1 info{};modern->GetDesc1(&info);hdr=info.ColorSpace==DXGI_COLOR_SPACE_RGB_FULL_G2084_NONE_P2020;}break;}}
    DXGI_SWAP_CHAIN_DESC1 desc{};desc.Format=hdr?DXGI_FORMAT_R16G16B16A16_FLOAT:DXGI_FORMAT_B8G8R8A8_UNORM;
    desc.SampleDesc.Count=1;desc.BufferUsage=DXGI_USAGE_RENDER_TARGET_OUTPUT;desc.BufferCount=2;
    desc.SwapEffect=DXGI_SWAP_EFFECT_FLIP_SEQUENTIAL;desc.Scaling=DXGI_SCALING_STRETCH;desc.AlphaMode=DXGI_ALPHA_MODE_IGNORE;
    desc.Flags=DXGI_SWAP_CHAIN_FLAG_FRAME_LATENCY_WAITABLE_OBJECT;
    hrCheck(factory->CreateSwapChainForHwnd(device.Get(),h,&desc,nullptr,nullptr,&swap),"Create swap chain");
    ComPtr<IDXGISwapChain2> pacing;hrCheck(swap.As(&pacing),"Get frame pacing interface");hrCheck(pacing->SetMaximumFrameLatency(1),"Set frame latency");frameLatency=pacing->GetFrameLatencyWaitableObject();
    if(!frameLatency)throw std::runtime_error("No DXGI frame pacing signal.");
    factory->MakeWindowAssociation(h,DXGI_MWA_NO_ALT_ENTER);
    if(hdr){ComPtr<IDXGISwapChain3> sc;hrCheck(swap.As(&sc),"HDR swap chain");hrCheck(sc->SetColorSpace1(DXGI_COLOR_SPACE_RGB_FULL_G10_NONE_P709),"Set scRGB color space");}
    D2D1_FACTORY_OPTIONS fo{};
    hrCheck(D2D1CreateFactory(D2D1_FACTORY_TYPE_SINGLE_THREADED,__uuidof(ID2D1Factory1),&fo,reinterpret_cast<void**>(d2dFactory.GetAddressOf())),"Create Direct2D factory");
    ComPtr<IDXGIDevice> dxgi;hrCheck(device.As(&dxgi),"DXGI device");
    hrCheck(d2dFactory->CreateDevice(dxgi.Get(),&d2dDevice),"Create D2D device");
    hrCheck(d2dDevice->CreateDeviceContext(D2D1_DEVICE_CONTEXT_OPTIONS_NONE,&d2d),"Create D2D context");
    hrCheck(d2d->CreateSolidColorBrush(D2D1::ColorF(1,1,1),&brush),"Create brush");
    hrCheck(DWriteCreateFactory(DWRITE_FACTORY_TYPE_SHARED,__uuidof(IDWriteFactory),reinterpret_cast<IUnknown**>(write.GetAddressOf())),"Create DirectWrite");
    auto vs=resource(101),ps=resource(102);
    hrCheck(device->CreateVertexShader(vs.data(),vs.size(),nullptr,&fieldVS),"Field vertex shader");
    hrCheck(device->CreatePixelShader(ps.data(),ps.size(),nullptr,&fieldPS),"Field pixel shader");
    vs=resource(103);ps=resource(104);
    hrCheck(device->CreateVertexShader(vs.data(),vs.size(),nullptr,&flowVS),"Flow vertex shader");
    hrCheck(device->CreatePixelShader(ps.data(),ps.size(),nullptr,&flowPS),"Flow pixel shader");
    D3D11_BUFFER_DESC ub{};ub.ByteWidth=48;ub.Usage=D3D11_USAGE_DEFAULT;ub.BindFlags=D3D11_BIND_CONSTANT_BUFFER;
    hrCheck(device->CreateBuffer(&ub,nullptr,&uniforms),"Create field uniforms");
    D3D11_RASTERIZER_DESC rd{};rd.FillMode=D3D11_FILL_SOLID;rd.CullMode=D3D11_CULL_NONE;rd.DepthClipEnable=TRUE;rd.AntialiasedLineEnable=TRUE;
    hrCheck(device->CreateRasterizerState(&rd,&raster),"Create raster state");
    D3D11_BLEND_DESC bd{};auto& rt=bd.RenderTarget[0];rt.BlendEnable=TRUE;rt.SrcBlend=D3D11_BLEND_SRC_ALPHA;rt.DestBlend=D3D11_BLEND_INV_SRC_ALPHA;
    rt.BlendOp=D3D11_BLEND_OP_ADD;rt.SrcBlendAlpha=D3D11_BLEND_ONE;rt.DestBlendAlpha=D3D11_BLEND_INV_SRC_ALPHA;rt.BlendOpAlpha=D3D11_BLEND_OP_ADD;rt.RenderTargetWriteMask=D3D11_COLOR_WRITE_ENABLE_ALL;
    hrCheck(device->CreateBlendState(&bd,&blend),"Create flow blend state");
    createTarget();
}
Renderer::~Renderer(){
    if(context){context->ClearState();context->Flush();}
    if(interop)cudaGraphicsUnregisterResource(interop);
    if(copyStream)cudaStreamDestroy(copyStream);
    if(frameLatency)CloseHandle(frameLatency);
}
void Renderer::createTarget(){
    hrCheck(swap->GetBuffer(0,IID_PPV_ARGS(&back)),"Get swap buffer");D3D11_TEXTURE2D_DESC desc{};back->GetDesc(&desc);width=desc.Width;height=desc.Height;
    hrCheck(device->CreateRenderTargetView(back.Get(),nullptr,&target),"Create backbuffer target");
    ComPtr<IDXGISurface> surface;hrCheck(back.As(&surface),"Get DXGI surface");
    D2D1_BITMAP_PROPERTIES1 props=D2D1::BitmapProperties1(D2D1_BITMAP_OPTIONS_TARGET|D2D1_BITMAP_OPTIONS_CANNOT_DRAW,D2D1::PixelFormat(desc.Format,D2D1_ALPHA_MODE_IGNORE));
    hrCheck(d2d->CreateBitmapFromDxgiSurface(surface.Get(),&props,&bitmap),"Create D2D surface");d2d->SetTarget(bitmap.Get());
}
void Renderer::resize(UINT w,UINT h){if(!w||!h||(w==width&&h==height))return;context->ClearState();d2d->SetTarget(nullptr);bitmap.Reset();target.Reset();back.Reset();context->Flush();
    hrCheck(swap->ResizeBuffers(0,w,h,DXGI_FORMAT_UNKNOWN,DXGI_SWAP_CHAIN_FLAG_FRAME_LATENCY_WAITABLE_OBJECT),"Resize swap chain");createTarget();}
void Renderer::begin(float scale,bool clear){d2d->SetDpi(96*scale,96*scale);d2d->SetTransform(D2D1::Matrix3x2F::Identity());d2d->BeginDraw();if(clear)d2d->Clear(linearColor(D2D1::ColorF(.035f,.047f,.073f),hdr));}
void Renderer::end(){hrCheck(d2d->EndDraw(),"Draw interface");}
void Renderer::present(){hrCheck(swap->Present(1,0),"Present frame");}
void Renderer::rectangle(D2D1_RECT_F rect,D2D1_COLOR_F color,float radius,bool outline){brush->SetColor(linearColor(color,hdr));if(radius>0){auto r=D2D1::RoundedRect(rect,radius,radius);if(outline)d2d->DrawRoundedRectangle(r,brush.Get());else d2d->FillRoundedRectangle(r,brush.Get());}else if(outline)d2d->DrawRectangle(rect,brush.Get());else d2d->FillRectangle(rect,brush.Get());}
void Renderer::circle(float x,float y,float radius,D2D1_COLOR_F color,bool outline){brush->SetColor(linearColor(color,hdr));auto shape=D2D1::Ellipse(D2D1::Point2F(x,y),radius,radius);if(outline)d2d->DrawEllipse(shape,brush.Get());else d2d->FillEllipse(shape,brush.Get());}
void Renderer::headerIcon(float x,float y){
    if(!headerSymbol){
        auto bytes=resource(IDR_HEADER_ICON);
        ComPtr<IStream> stream;hrCheck(CreateStreamOnHGlobal(nullptr,TRUE,&stream),"Create header icon stream");
        hrCheck(stream->Write(bytes.data(),ULONG(bytes.size()),nullptr),"Read header icon");
        hrCheck(stream->Seek({},STREAM_SEEK_SET,nullptr),"Rewind header icon");
        hrCheck(d2d.As(&iconContext),"Get SVG drawing context");
        hrCheck(iconContext->CreateSvgDocument(stream.Get(),D2D1::SizeF(42,42),&headerSymbol),"Create header icon");
    }
    d2d->SetTransform(D2D1::Matrix3x2F::Translation(x,y));
    iconContext->DrawSvgDocument(headerSymbol.Get());
    d2d->SetTransform(D2D1::Matrix3x2F::Identity());
}
void Renderer::line(float x1,float y1,float x2,float y2,D2D1_COLOR_F color,float thickness){brush->SetColor(linearColor(color,hdr));d2d->DrawLine(D2D1::Point2F(x1,y1),D2D1::Point2F(x2,y2),brush.Get(),thickness);}
void Renderer::text(const std::wstring& value,D2D1_RECT_F rect,float size,D2D1_COLOR_F color,bool mono,bool right){
    auto key=std::make_pair(int(size*10),mono);auto& font=fonts[key];if(!font){hrCheck(write->CreateTextFormat(mono?L"Consolas":L"Segoe UI",nullptr,DWRITE_FONT_WEIGHT_NORMAL,DWRITE_FONT_STYLE_NORMAL,DWRITE_FONT_STRETCH_NORMAL,size,L"en-us",&font),"Create font");font->SetWordWrapping(DWRITE_WORD_WRAPPING_NO_WRAP);font->SetParagraphAlignment(DWRITE_PARAGRAPH_ALIGNMENT_CENTER);}
    font->SetTextAlignment(right?DWRITE_TEXT_ALIGNMENT_TRAILING:DWRITE_TEXT_ALIGNMENT_LEADING);brush->SetColor(linearColor(color,hdr));d2d->DrawTextW(value.c_str(),UINT32(value.size()),font.Get(),rect,brush.Get(),D2D1_DRAW_TEXT_OPTIONS_CLIP);
}
void Renderer::elidedText(const std::wstring& value,D2D1_RECT_F rect,float size,D2D1_COLOR_F color){
    if(!subtitleLayout||subtitleValue!=value){
        ComPtr<IDWriteTextFormat> font;ComPtr<IDWriteInlineObject> ellipsis;
        hrCheck(write->CreateTextFormat(L"Consolas",nullptr,DWRITE_FONT_WEIGHT_NORMAL,DWRITE_FONT_STYLE_NORMAL,DWRITE_FONT_STRETCH_NORMAL,size,L"en-us",&font),"Create subtitle font");
        font->SetWordWrapping(DWRITE_WORD_WRAPPING_NO_WRAP);font->SetParagraphAlignment(DWRITE_PARAGRAPH_ALIGNMENT_CENTER);
        hrCheck(write->CreateEllipsisTrimmingSign(font.Get(),&ellipsis),"Create subtitle ellipsis");
        DWRITE_TRIMMING trimming{DWRITE_TRIMMING_GRANULARITY_CHARACTER,0,0};font->SetTrimming(&trimming,ellipsis.Get());
        subtitleLayout.Reset();hrCheck(write->CreateTextLayout(value.c_str(),UINT32(value.size()),font.Get(),rect.right-rect.left,rect.bottom-rect.top,&subtitleLayout),"Create subtitle layout");
        subtitleValue=value;
    }
    subtitleLayout->SetMaxWidth(rect.right-rect.left);subtitleLayout->SetMaxHeight(rect.bottom-rect.top);
    brush->SetColor(linearColor(color,hdr));d2d->DrawTextLayout(D2D1::Point2F(rect.left,rect.top),subtitleLayout.Get(),brush.Get(),D2D1_DRAW_TEXT_OPTIONS_CLIP);
}
void Renderer::title(float x,float y,D2D1_COLOR_F color){
    if(!titleLayout){
        ComPtr<IDWriteTextFormat> font;ComPtr<IDWriteTextLayout> layout;
        hrCheck(write->CreateTextFormat(L"Segoe UI",nullptr,DWRITE_FONT_WEIGHT_SEMI_BOLD,DWRITE_FONT_STYLE_NORMAL,DWRITE_FONT_STRETCH_NORMAL,16,L"en-us",&font),"Create title font");
        font->SetParagraphAlignment(DWRITE_PARAGRAPH_ALIGNMENT_CENTER);
        hrCheck(write->CreateTextLayout(L"2D FAST TURBO",13,font.Get(),255,23,&layout),"Create title layout");
        hrCheck(layout.As(&titleLayout),"Title spacing interface");
        titleLayout->SetCharacterSpacing(1.3f,1.3f,0,{0,13});
    }
    brush->SetColor(linearColor(color,hdr));d2d->DrawTextLayout(D2D1::Point2F(x,y),titleLayout.Get(),brush.Get());
}
void Renderer::gradient(D2D1_RECT_F rect,int palette){
    static const D2D1_COLOR_F colors[4][3]={{{.22f,.25f,.82f,1},{.06f,.09f,.18f,1},{.39f,.94f,.73f,1}},{{.42f,.14f,.57f,1},{.12f,.04f,.1f,1},{1,.64f,.27f,1}},{{.12f,.29f,.58f,1},{.03f,.12f,.19f,1},{.64f,.94f,1,1}},{{.06f,.03f,.16f,1},{.68f,.16f,.28f,1},{.99f,.93f,.56f,1}}};
    D2D1_GRADIENT_STOP stops[3];for(int i=0;i<3;++i)stops[i]={i*.5f,linearColor(colors[palette][i],hdr)};
    ComPtr<ID2D1GradientStopCollection> collection;hrCheck(d2d->CreateGradientStopCollection(stops,3,D2D1_GAMMA_1_0,D2D1_EXTEND_MODE_CLAMP,&collection),"Palette stops");
    ComPtr<ID2D1LinearGradientBrush> gradient;hrCheck(d2d->CreateLinearGradientBrush(D2D1::LinearGradientBrushProperties(D2D1::Point2F(rect.left,rect.top),D2D1::Point2F(rect.right,rect.top)),collection.Get(),&gradient),"Palette gradient");
    d2d->FillRoundedRectangle(D2D1::RoundedRect(rect,3,3),gradient.Get());
}
void Renderer::clip(D2D1_RECT_F rect){d2d->PushAxisAlignedClip(rect,D2D1_ANTIALIAS_MODE_PER_PRIMITIVE);}
void Renderer::unclip(){d2d->PopAxisAlignedClip();}
void Renderer::upload(const std::shared_ptr<Frame>& frame) {
    if(uploaded==frame)return;
    if(!frame)throw std::runtime_error("No completed field to render.");
    ID3D11ShaderResourceView* nullView=nullptr;context->VSSetShaderResources(0,1,&nullView);context->PSSetShaderResources(0,1,&nullView);
    if(frame->n!=fieldN){
        if(interop){CUDA_CHECK(cudaGraphicsUnregisterResource(interop));interop=nullptr;}
        fieldView.Reset();fields.Reset();D3D11_BUFFER_DESC bd{};bd.ByteWidth=UINT(size_t(3)*frame->n*frame->n*4);
        bd.Usage=D3D11_USAGE_DEFAULT;bd.BindFlags=D3D11_BIND_SHADER_RESOURCE;bd.MiscFlags=D3D11_RESOURCE_MISC_BUFFER_ALLOW_RAW_VIEWS;
        hrCheck(device->CreateBuffer(&bd,nullptr,&fields),"Create shared CUDA field");
        D3D11_SHADER_RESOURCE_VIEW_DESC sv{};sv.Format=DXGI_FORMAT_R32_TYPELESS;sv.ViewDimension=D3D11_SRV_DIMENSION_BUFFEREX;sv.BufferEx.NumElements=bd.ByteWidth/4;sv.BufferEx.Flags=D3D11_BUFFEREX_SRV_FLAG_RAW;
        hrCheck(device->CreateShaderResourceView(fields.Get(),&sv,&fieldView),"Shared field view");
        CUDA_CHECK(cudaGraphicsD3D11RegisterResource(&interop,fields.Get(),cudaGraphicsRegisterFlagsNone));
        CUDA_CHECK(cudaGraphicsResourceSetMapFlags(interop,cudaGraphicsMapFlagsWriteDiscard));fieldN=frame->n;
    }
    CUDA_CHECK(cudaGraphicsMapResources(1,&interop,copyStream));bool mapped=true;
    try {void* pointer=nullptr;size_t size=0;CUDA_CHECK(cudaGraphicsResourceGetMappedPointer(&pointer,&size,interop));
        size_t bytes=size_t(3)*frame->n*frame->n*4;if(size<bytes)throw std::runtime_error("Shared field buffer too small.");
        CUDA_CHECK(cudaMemcpyAsync(pointer,frame->data(),bytes,cudaMemcpyDeviceToDevice,copyStream));
        CUDA_CHECK(cudaGraphicsUnmapResources(1,&interop,copyStream));mapped=false;
        CUDA_CHECK(cudaStreamSynchronize(copyStream));uploaded=frame;
    }catch(...){if(mapped)cudaGraphicsUnmapResources(1,&interop,copyStream);throw;}
}
void Renderer::field(ID3D11RenderTargetView* rt,const std::shared_ptr<Frame>& f,const Display& d,float x,float y,float side,bool output,float radius){
    upload(f);
    struct Uniforms {UINT n,palette,display,flow;float exposure,omegaScale,speedScale,mean;UINT range,hdr;float side,radius;};
    Uniforms u{UINT(f->n),UINT(d.palette),UINT(d.field),UINT(d.flow),d.exposure,d.field==0&&d.colorRange==0?std::max(d.sigma*f->sigma,1e-12f):std::max(f->maxOmega,.0001f),std::max(f->maxSpeed,.0001f),f->mean,UINT(d.colorRange),UINT(output),side,radius};
    context->UpdateSubresource(uniforms.Get(),0,nullptr,&u,0,0);
    D3D11_VIEWPORT viewport{x,y,side,side,0,1};context->RSSetViewports(1,&viewport);context->RSSetState(raster.Get());
    context->OMSetRenderTargets(1,&rt,nullptr);context->OMSetDepthStencilState(nullptr,0);context->OMSetBlendState(nullptr,nullptr,~0u);
    context->IASetInputLayout(nullptr);context->IASetPrimitiveTopology(D3D11_PRIMITIVE_TOPOLOGY_TRIANGLELIST);
    context->VSSetShader(fieldVS.Get(),nullptr,0);context->PSSetShader(fieldPS.Get(),nullptr,0);
    auto constant=uniforms.Get();context->VSSetConstantBuffers(0,1,&constant);context->PSSetConstantBuffers(0,1,&constant);
    auto srv=fieldView.Get();context->VSSetShaderResources(0,1,&srv);context->PSSetShaderResources(0,1,&srv);
    context->Draw(3,0);
    if(d.flow&&f->hasVelocity){context->OMSetBlendState(blend.Get(),nullptr,~0u);context->IASetPrimitiveTopology(D3D11_PRIMITIVE_TOPOLOGY_LINELIST);context->VSSetShader(flowVS.Get(),nullptr,0);context->PSSetShader(flowPS.Get(),nullptr,0);context->Draw(32*32*18*2,0);}
    ID3D11ShaderResourceView* nullView=nullptr;context->VSSetShaderResources(0,1,&nullView);context->PSSetShaderResources(0,1,&nullView);
    context->OMSetRenderTargets(0,nullptr,nullptr);
}
void Renderer::drawField(const std::shared_ptr<Frame>& frame,const Display& display,float x,float y,float side,float scale){if(frame)field(target.Get(),frame,display,x*scale,y*scale,side*scale,hdr,15*scale);}
void Renderer::saveTexture(ID3D11Texture2D* texture,const std::filesystem::path& path){
    D3D11_TEXTURE2D_DESC desc{};texture->GetDesc(&desc);desc.Usage=D3D11_USAGE_STAGING;desc.BindFlags=0;desc.MiscFlags=0;desc.CPUAccessFlags=D3D11_CPU_ACCESS_READ;
    ComPtr<ID3D11Texture2D> staging;hrCheck(device->CreateTexture2D(&desc,nullptr,&staging),"Create PNG readback");context->CopyResource(staging.Get(),texture);
    D3D11_MAPPED_SUBRESOURCE mapped{};hrCheck(context->Map(staging.Get(),0,D3D11_MAP_READ,0,&mapped),"Read PNG pixels");
    std::vector<unsigned char> pixels(size_t(desc.Width)*desc.Height*4);
    for(UINT y=0;y<desc.Height;++y){auto src=static_cast<const unsigned char*>(mapped.pData)+size_t(y)*mapped.RowPitch;auto dst=pixels.data()+size_t(y)*desc.Width*4;
        if(desc.Format==DXGI_FORMAT_B8G8R8A8_UNORM)std::memcpy(dst,src,size_t(desc.Width)*4);
        else {auto half=reinterpret_cast<const uint16_t*>(src);for(UINT x=0;x<desc.Width;++x){for(int c=0;c<3;++c){float v=std::clamp(DirectX::PackedVector::XMConvertHalfToFloat(half[x*4+c]),0.f,1.f);float srgb=v<=.0031308f?12.92f*v:1.055f*std::pow(v,1/2.4f)-.055f;dst[x*4+2-c]=static_cast<unsigned char>(srgb*255+.5f);}dst[x*4+3]=255;}}
    }
    context->Unmap(staging.Get(),0);
    ComPtr<IWICImagingFactory> wic;hrCheck(CoCreateInstance(CLSID_WICImagingFactory,nullptr,CLSCTX_INPROC_SERVER,IID_PPV_ARGS(&wic)),"Create PNG encoder factory");
    ComPtr<IWICStream> stream;hrCheck(wic->CreateStream(&stream),"Create PNG stream");hrCheck(stream->InitializeFromFilename(path.c_str(),GENERIC_WRITE),"Open PNG");
    ComPtr<IWICBitmapEncoder> encoder;hrCheck(wic->CreateEncoder(GUID_ContainerFormatPng,nullptr,&encoder),"Create PNG encoder");hrCheck(encoder->Initialize(stream.Get(),WICBitmapEncoderNoCache),"Initialize PNG encoder");
    ComPtr<IWICBitmapFrameEncode> frame;hrCheck(encoder->CreateNewFrame(&frame,nullptr),"Create PNG frame");hrCheck(frame->Initialize(nullptr),"Initialize PNG frame");
    hrCheck(frame->SetSize(desc.Width,desc.Height),"PNG size");WICPixelFormatGUID format=GUID_WICPixelFormat32bppBGRA;hrCheck(frame->SetPixelFormat(&format),"PNG format");
    hrCheck(frame->WritePixels(desc.Height,desc.Width*4,UINT(pixels.size()),pixels.data()),"Write PNG pixels");hrCheck(frame->Commit(),"Commit PNG frame");hrCheck(encoder->Commit(),"Commit PNG file");
}
void Renderer::exportPNG(const std::shared_ptr<Frame>& f,const Display& d,const std::filesystem::path& path,int side){
    D3D11_TEXTURE2D_DESC desc{};desc.Width=desc.Height=side;desc.MipLevels=desc.ArraySize=1;desc.Format=DXGI_FORMAT_B8G8R8A8_UNORM;desc.SampleDesc.Count=1;desc.BindFlags=D3D11_BIND_RENDER_TARGET;
    ComPtr<ID3D11Texture2D> texture;hrCheck(device->CreateTexture2D(&desc,nullptr,&texture),"Create PNG render target");ComPtr<ID3D11RenderTargetView> rt;hrCheck(device->CreateRenderTargetView(texture.Get(),nullptr,&rt),"PNG target view");
    field(rt.Get(),f,d,0,0,float(side),false,0);saveTexture(texture.Get(),path);
}
void Renderer::capturePNG(const std::filesystem::path& path){saveTexture(back.Get(),path);}
}
