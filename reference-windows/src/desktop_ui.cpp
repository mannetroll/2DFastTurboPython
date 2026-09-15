#include "desktop_ui.hpp"
#include "desktop_resources.h"
#include "resources.hpp"
#include "cuda_support.cuh"
#include <dwmapi.h>
#include <wincodec.h>
#include <wrl/client.h>
#include <iomanip>
#include <sstream>
#include <vector>
#include <cwctype>

namespace turbo {
namespace {
using Microsoft::WRL::ComPtr;
constexpr COLORREF background=RGB(16,20,28), foreground=RGB(230,232,236);
std::wstring wide(const char* text) {
    int length=MultiByteToWideChar(CP_UTF8,0,text,-1,nullptr,0);
    std::wstring result(length,L'\0');
    MultiByteToWideChar(CP_UTF8,0,text,-1,result.data(),length);
    if(!result.empty())result.pop_back();
    return result;
}
std::wstring cudaVersion(int value) {return std::to_wstring(value/1000)+L"."+std::to_wstring(value%1000/10);}
void check(HRESULT result) {if(FAILED(result))throw std::runtime_error("Cannot decode the embedded Navier–Stokes equation.");}

struct AboutState {
    HBRUSH brush=CreateSolidBrush(background);
    ComPtr<IWICImagingFactory> factory;
    ComPtr<IWICBitmapClipper> equation;
    UINT width=0,height=0;
    bool painted=false;
    AboutShown shown=nullptr;
    ~AboutState(){DeleteObject(brush);}
    void load() {
        auto png=resource(IDR_EQUATION);
        check(CoCreateInstance(CLSID_WICImagingFactory,nullptr,CLSCTX_INPROC_SERVER,IID_PPV_ARGS(&factory)));
        ComPtr<IWICStream> stream;check(factory->CreateStream(&stream));
        check(stream->InitializeFromMemory(reinterpret_cast<BYTE*>(const_cast<char*>(png.data())),DWORD(png.size())));
        ComPtr<IWICBitmapDecoder> decoder;check(factory->CreateDecoderFromStream(stream.Get(),nullptr,WICDecodeMetadataCacheOnLoad,&decoder));
        ComPtr<IWICBitmapFrameDecode> frame;check(decoder->GetFrame(0,&frame));
        ComPtr<IWICFormatConverter> rgba;check(factory->CreateFormatConverter(&rgba));
        check(rgba->Initialize(frame.Get(),GUID_WICPixelFormat32bppBGRA,WICBitmapDitherTypeNone,nullptr,0,WICBitmapPaletteTypeCustom));
        check(rgba->GetSize(&width,&height));
        std::vector<BYTE> pixels(size_t(width)*height*4);check(rgba->CopyPixels(nullptr,width*4,UINT(pixels.size()),pixels.data()));
        UINT left=width,top=height,right=0,bottom=0;
        for(UINT y=0;y<height;++y)for(UINT x=0;x<width;++x)if(pixels[(size_t(y)*width+x)*4+3]){
            left=std::min(left,x);top=std::min(top,y);right=std::max(right,x+1);bottom=std::max(bottom,y+1);
        }
        if(right<=left||bottom<=top)throw std::runtime_error("The equation image is empty.");
        WICRect crop{int(left),int(top),int(right-left),int(bottom-top)};
        check(factory->CreateBitmapClipper(&equation));check(equation->Initialize(rgba.Get(),&crop));
        width=right-left;height=bottom-top;
    }
    void draw(const DRAWITEMSTRUCT& item) {
        FillRect(item.hDC,&item.rcItem,brush);
        if(!equation)return;
        double scale=std::min(double(item.rcItem.right-item.rcItem.left)/width,double(item.rcItem.bottom-item.rcItem.top)/height);
        UINT w=std::max(1u,UINT(width*scale)),h=std::max(1u,UINT(height*scale));
        ComPtr<IWICBitmapScaler> scaled;check(factory->CreateBitmapScaler(&scaled));
        check(scaled->Initialize(equation.Get(),w,h,WICBitmapInterpolationModeFant));
        std::vector<BYTE> pixels(size_t(w)*h*4);check(scaled->CopyPixels(nullptr,w*4,UINT(pixels.size()),pixels.data()));
        for(size_t i=0;i<pixels.size();i+=4){unsigned a=pixels[i+3];
            for(unsigned channel=0;channel<3;++channel){unsigned shift=8*(2-channel);
                unsigned fg=(foreground>>shift)&255,bg=(background>>shift)&255;
                pixels[i+channel]=BYTE((fg*a+bg*(255-a)+127)/255);
            }
            pixels[i+3]=255;
        }
        BITMAPINFO bitmap{};bitmap.bmiHeader={sizeof(BITMAPINFOHEADER),LONG(w),-LONG(h),1,32,BI_RGB,0,0,0,0,0};
        int x=item.rcItem.left+(item.rcItem.right-item.rcItem.left-int(w))/2;
        int y=item.rcItem.top+(item.rcItem.bottom-item.rcItem.top-int(h))/2;
        painted=SetDIBitsToDevice(item.hDC,x,y,w,h,0,0,0,h,pixels.data(),&bitmap,DIB_RGB_COLORS)!=0;
    }
};
INT_PTR CALLBACK aboutProc(HWND window,UINT message,WPARAM w,LPARAM l) {
    auto state=reinterpret_cast<AboutState*>(GetWindowLongPtrW(window,DWLP_USER));
    if(message==WM_INITDIALOG){
        state=reinterpret_cast<AboutState*>(l);SetWindowLongPtrW(window,DWLP_USER,l);
        BOOL dark=TRUE;DwmSetWindowAttribute(window,20,&dark,sizeof(dark));
        HICON icon=LoadIconW(GetModuleHandleW(nullptr),MAKEINTRESOURCEW(1));
        SendMessageW(window,WM_SETICON,ICON_BIG,reinterpret_cast<LPARAM>(icon));
        SendMessageW(window,WM_SETICON,ICON_SMALL,reinterpret_cast<LPARAM>(icon));
        HWND iconControl=GetDlgItem(window,IDC_ABOUT_ICON);RECT iconRect{};GetClientRect(iconControl,&iconRect);
        auto largeIcon=LoadImageW(GetModuleHandleW(nullptr),MAKEINTRESOURCEW(1),IMAGE_ICON,
            iconRect.right,iconRect.bottom,LR_SHARED);
        SendMessageW(iconControl,STM_SETICON,reinterpret_cast<WPARAM>(largeIcon),0);
        SendMessageW(window,DM_SETDEFID,IDOK,0);
        RECT dialog{},owner{};GetWindowRect(window,&dialog);GetWindowRect(GetParent(window),&owner);
        SetWindowPos(window,nullptr,owner.left+(owner.right-owner.left-(dialog.right-dialog.left))/2,
            owner.top+(owner.bottom-owner.top-(dialog.bottom-dialog.top))/2,0,0,SWP_NOSIZE|SWP_NOZORDER);
        if(state->shown)SetTimer(window,1,200,nullptr);
        return TRUE;
    }
    if(!state)return FALSE;
    if(message==WM_CTLCOLORDLG||message==WM_CTLCOLORSTATIC){
        auto dc=reinterpret_cast<HDC>(w);SetTextColor(dc,foreground);SetBkColor(dc,background);return reinterpret_cast<INT_PTR>(state->brush);
    }
    if(message==WM_DRAWITEM&&w==IDC_EQUATION){
        try{state->draw(*reinterpret_cast<DRAWITEMSTRUCT*>(l));}catch(const std::exception&){state->painted=false;}
        return TRUE;
    }
    if(message==WM_DRAWITEM&&w==IDOK){
        const auto& button=*reinterpret_cast<DRAWITEMSTRUCT*>(l);
        int saved=SaveDC(button.hDC);SetDCBrushColor(button.hDC,button.itemState&ODS_SELECTED?RGB(45,66,72):RGB(32,41,53));
        FillRect(button.hDC,&button.rcItem,static_cast<HBRUSH>(GetStockObject(DC_BRUSH)));
        SetTextColor(button.hDC,foreground);SetBkMode(button.hDC,TRANSPARENT);
        SelectObject(button.hDC,reinterpret_cast<HFONT>(SendMessageW(window,WM_GETFONT,0,0)));
        RECT rect=button.rcItem;DrawTextW(button.hDC,L"OK",2,&rect,DT_CENTER|DT_VCENTER|DT_SINGLELINE);
        if(button.itemState&ODS_FOCUS){InflateRect(&rect,-3,-3);DrawFocusRect(button.hDC,&rect);}
        RestoreDC(button.hDC,saved);return TRUE;
    }
    if(message==WM_TIMER&&w==1){KillTimer(window,1);state->shown(window);return TRUE;}
    if(message==WM_COMMAND&&(LOWORD(w)==IDOK||LOWORD(w)==IDCANCEL)){EndDialog(window,LOWORD(w));return TRUE;}
    return FALSE;
}
}

RuntimeEnvironment runtimeEnvironment(int device) {
    cudaDeviceProp properties{};CUDA_CHECK(cudaGetDeviceProperties(&properties,device));
    int runtime=0,driver=0;CUDA_CHECK(cudaRuntimeGetVersion(&runtime));CUDA_CHECK(cudaDriverGetVersion(&driver));
    RuntimeEnvironment result;result.gpu=wide(properties.name);
    auto gpu=result.gpu;const std::wstring prefix=L"NVIDIA GeForce ";if(gpu.starts_with(prefix))gpu.erase(0,prefix.size());
    std::wostringstream memory;memory<<std::fixed<<std::setprecision(1)<<double(properties.totalGlobalMem)/(1ull<<30)<<L" GiB";
    // RtlGetVersion reports the installed build independently of compatibility manifests.
    OSVERSIONINFOW version{};version.dwOSVersionInfoSize=sizeof(version);
    using VersionFunction=LONG (WINAPI*)(OSVERSIONINFOW*);
    auto query=reinterpret_cast<VersionFunction>(GetProcAddress(GetModuleHandleW(L"ntdll.dll"),"RtlGetVersion"));
    bool known=query&&query(&version)==0;
    std::wstring os=known?(version.dwMajorVersion>=10?(version.dwBuildNumber>=22000?L"Win 11":L"Win 10"):L"Windows "+std::to_wstring(version.dwMajorVersion)):L"Windows";
    result.subtitle=gpu+L" · "+memory.str()+L" · CUDA "+cudaVersion(runtime)+L" · "+os;
    result.details=L"GPU: "+result.gpu+L"\nGPU memory: "+memory.str()+L"\nCUDA runtime: "+cudaVersion(runtime)+L"\nCUDA driver: "+cudaVersion(driver)
        +L"\nOS: "+os+(known?L" (build "+std::to_wstring(version.dwBuildNumber)+L")":L"")+L"\nNative CUDA / VkFFT · x64";
    return result;
}

HMENU createDesktopMenus() {
    HMENU bar=CreateMenu();
    auto menu=[&](const wchar_t* label){HMENU result=CreatePopupMenu();AppendMenuW(bar,MF_POPUP,reinterpret_cast<UINT_PTR>(result),label);return result;};
    auto item=[](HMENU target,UINT id,const wchar_t* label){AppendMenuW(target,MF_STRING,id,label);};
    auto separator=[](HMENU target){AppendMenuW(target,MF_SEPARATOR,0,nullptr);};
    auto file=menu(L"&File");item(file,SaveSnapshot,L"&Save Snapshot…\tCtrl+S");item(file,CloseWindow,L"&Close Window\tCtrl+W");
    separator(file);item(file,About,L"&About 2DFastTurbo");separator(file);item(file,Quit,L"&Quit 2DFastTurbo\tCtrl+Q");
    auto edit=menu(L"&Edit");item(edit,Undo,L"&Undo\tCtrl+Z");item(edit,Redo,L"&Redo\tCtrl+Y");separator(edit);
    item(edit,Cut,L"Cu&t\tCtrl+X");item(edit,Copy,L"&Copy\tCtrl+C");item(edit,Paste,L"&Paste\tCtrl+V");item(edit,SelectAll,L"Select &All\tCtrl+A");
    auto simulation=menu(L"&Simulation");item(simulation,ToggleRunning,L"&Pause\tSpace");item(simulation,SingleStep,L"Single &Step\tRight");
    separator(simulation);item(simulation,ResetFlow,L"&Reset Flow\tR");item(simulation,NewSeed,L"&New Initial Condition\tCtrl+Shift+R");
    auto view=menu(L"&View");item(view,FocusFlow,L"&Focus on Flow\tF");item(view,ShowSimulation,L"&Show Simulation");item(view,DismissMessage,L"&Dismiss Message");
    auto window=menu(L"&Window");item(window,Minimize,L"&Minimize\tCtrl+M");
    return bar;
}

MenuTheme::MenuTheme():brush(CreateSolidBrush(background)){}
MenuTheme::~MenuTheme(){if(font)DeleteObject(font);DeleteObject(brush);}
HFONT MenuTheme::menuFont(HWND owner) {
    UINT dpi=GetDpiForWindow(owner);
    if(!font||fontDpi!=dpi){
        if(font)DeleteObject(font);fontDpi=dpi;
        NONCLIENTMETRICSW metrics{sizeof(metrics)};
        SystemParametersInfoForDpi(SPI_GETNONCLIENTMETRICS,sizeof(metrics),&metrics,0,dpi);
        font=CreateFontIndirectW(&metrics.lfMenuFont);
    }
    return font;
}
void MenuTheme::apply(HMENU menu,bool bar) {
    MENUINFO style{sizeof(style)};style.fMask=MIM_BACKGROUND;style.hbrBack=brush;SetMenuInfo(menu,&style);
    for(int index=0;index<GetMenuItemCount(menu);++index){
        wchar_t label[256]{};MENUITEMINFOW info{sizeof(info)};
        info.fMask=MIIM_FTYPE|MIIM_SUBMENU|MIIM_STRING;info.dwTypeData=label;info.cch=256;
        GetMenuItemInfoW(menu,UINT(index),TRUE,&info);
        if(info.hSubMenu)apply(info.hSubMenu,false);
        if(info.fType&MFT_SEPARATOR)continue;
        items.emplace_back();auto& data=items.back();data.text=label;data.bar=bar;
        data.accessible={MSAA_MENU_SIG,DWORD(data.text.size()),data.text.data()};
        info.fMask=MIIM_FTYPE|MIIM_DATA;info.fType|=MFT_OWNERDRAW;info.dwItemData=reinterpret_cast<ULONG_PTR>(&data);
        SetMenuItemInfoW(menu,UINT(index),TRUE,&info);
    }
}
void MenuTheme::title(HMENU menu,UINT command,const wchar_t* label) {
    MENUITEMINFOW info{sizeof(info)};info.fMask=MIIM_DATA;GetMenuItemInfoW(menu,command,FALSE,&info);
    if(auto item=reinterpret_cast<Item*>(info.dwItemData)){
        item->text=label;item->accessible={MSAA_MENU_SIG,DWORD(item->text.size()),item->text.data()};
    }
    info.fMask=MIIM_STRING;info.dwTypeData=const_cast<wchar_t*>(label);SetMenuItemInfoW(menu,command,FALSE,&info);
}
LRESULT MenuTheme::measure(HWND owner,MEASUREITEMSTRUCT& measure) {
    if(measure.CtlType!=ODT_MENU||!measure.itemData)return FALSE;
    const auto& item=*reinterpret_cast<Item*>(measure.itemData);HDC dc=GetDC(owner);auto previous=SelectObject(dc,menuFont(owner));
    std::wstring text=item.text;std::replace(text.begin(),text.end(),L'\t',L' ');
    RECT rect{};DrawTextW(dc,text.c_str(),int(text.size()),&rect,DT_CALCRECT|DT_SINGLELINE);
    UINT dpi=GetDpiForWindow(owner);measure.itemWidth=UINT(rect.right)+MulDiv(item.bar?14:64,dpi,96);
    measure.itemHeight=item.bar?GetSystemMetricsForDpi(SM_CYMENU,dpi)-1:MulDiv(26,dpi,96);
    SelectObject(dc,previous);ReleaseDC(owner,dc);return TRUE;
}
LRESULT MenuTheme::draw(HWND owner,const DRAWITEMSTRUCT& draw) {
    if(draw.CtlType!=ODT_MENU||!draw.itemData)return FALSE;
    const auto& item=*reinterpret_cast<Item*>(draw.itemData);int saved=SaveDC(draw.hDC);
    SetDCBrushColor(draw.hDC,draw.itemState&ODS_SELECTED?RGB(38,53,63):background);
    FillRect(draw.hDC,&draw.rcItem,static_cast<HBRUSH>(GetStockObject(DC_BRUSH)));
    SetTextColor(draw.hDC,draw.itemState&ODS_DISABLED?RGB(100,110,123):foreground);SetBkMode(draw.hDC,TRANSPARENT);
    SelectObject(draw.hDC,menuFont(owner));RECT rect=draw.rcItem;int padding=MulDiv(item.bar?7:24,GetDpiForWindow(owner),96);rect.left+=padding;rect.right-=padding;
    size_t split=item.text.find(L'\t');std::wstring text=item.text.substr(0,split);
    UINT flags=DT_SINGLELINE|DT_VCENTER|(draw.itemState&ODS_NOACCEL?DT_HIDEPREFIX:0);
    DrawTextW(draw.hDC,text.c_str(),int(text.size()),&rect,flags);
    if(split!=std::wstring::npos){text=item.text.substr(split+1);DrawTextW(draw.hDC,text.c_str(),int(text.size()),&rect,flags|DT_RIGHT);}
    RestoreDC(draw.hDC,saved);return TRUE;
}
LRESULT MenuTheme::mnemonic(HMENU menu,wchar_t key) {
    for(int index=0;index<GetMenuItemCount(menu);++index){
        MENUITEMINFOW info{sizeof(info)};info.fMask=MIIM_DATA|MIIM_STATE;GetMenuItemInfoW(menu,UINT(index),TRUE,&info);
        if(auto item=reinterpret_cast<Item*>(info.dwItemData)){
            size_t mark=item->text.find(L'&');
            if(!(info.fState&MFS_DISABLED)&&mark+1<item->text.size()&&mark!=std::wstring::npos&&std::towupper(item->text[mark+1])==std::towupper(key))
                return MAKELRESULT(index,MNC_EXECUTE);
        }
    }
    return MAKELRESULT(0,MNC_IGNORE);
}

bool showAbout(HWND owner,AboutShown shown) {
    AboutState state;state.shown=shown;state.load();
    if(DialogBoxParamW(GetModuleHandleW(nullptr),MAKEINTRESOURCEW(IDD_ABOUT),owner,aboutProc,reinterpret_cast<LPARAM>(&state))==-1)
        throw std::runtime_error("Cannot open About 2DFastTurbo.");
    return state.painted;
}
}
