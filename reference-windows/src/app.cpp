#include "controller.hpp"
#include "renderer.hpp"
#include <windowsx.h>
#include <commdlg.h>
#include <dwmapi.h>
#include <shellapi.h>
#include <timeapi.h>
#include <fstream>
#include <functional>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <deque>

namespace turbo {
namespace {
using Rect=D2D1_RECT_F;
const auto foreground=D2D1::ColorF(.90f,.90f,.90f),secondary=D2D1::ColorF(.48f,.55f,.65f),accent=D2D1::ColorF(.44f,.91f,.82f);
const auto border=D2D1::ColorF(1,1,1,.075f),panel=D2D1::ColorF(.061f,.076f,.106f);
Rect box(float x,float y,float w,float h){return {x,y,x+w,y+h};}
bool contains(Rect r,float x,float y){return x>=r.left&&x<r.right&&y>=r.top&&y<r.bottom;}
std::wstring widen(const std::string& text){if(text.empty())return {};int n=MultiByteToWideChar(CP_UTF8,0,text.data(),int(text.size()),nullptr,0);std::wstring result(n,0);MultiByteToWideChar(CP_UTF8,0,text.data(),int(text.size()),result.data(),n);return result;}
std::wstring number(double value,int digits=3){std::wostringstream out;if(value!=0&&(std::abs(value)<.001||std::abs(value)>=10000))out<<std::scientific<<std::setprecision(2);else out<<std::fixed<<std::setprecision(digits);out<<value;return out.str();}
enum Id {Focus=1,Save,Play,Step,Reset,K0,Seed,Dice,Resolution,Re,Regulate,Enstrophy,Vorticity,Speed,Inferno,Ember,Aurora,Glacier,Range,Contrast,Flow,Method,Formulation,CFL,Dismiss};
struct Hit {int id;Rect rect;std::wstring help;bool enabled;bool slider;};
class App {
public:
    HWND hwnd{},edit{};
    Options options;
    std::unique_ptr<Renderer> renderer;
    std::unique_ptr<Controller> controller;
    Display display;
    ViewState view;
    float scale=1,side=640,scroll=0,mouseX=-100,mouseY=-100;
    bool focused=false,done=false,minimized=false,editing=false,livePending=false,exporting=false;
    int dragging=0,keyboardFocus=0,hoverId=0,exitCode=0;
    double hoverSince=0,lastLive=0;
    double nextDraw=0;
    float localCFL=3,localLogRe=4;
    std::vector<Hit> hits;
    std::string uiError;
    uint64_t seenGeneration=0,presentations=0,fieldFrames=0;
    std::shared_ptr<Frame> lastPresented;
    double firstPresentation=0,lastPresentation=0;
    HBRUSH editBrush{};
    HFONT editFont{};
    NOTIFYICONDATAW tray{};
    bool trayVisible=false;
    bool checking=false;
    bool measurementStarted=false;
    int automatedMenuSelection=-1;
    std::filesystem::path automatedSave;
    struct Check {std::string name;std::function<bool()> action;double started=0;};
    std::deque<Check> checks;
    std::vector<std::string> passed;
    uint64_t testStep=0,testGeneration=0;
    double testRate=0,testTime=0;
    float testSide=0;
    Config testConfig;
    explicit App(Options o):options(o),display(o.display){}
    ~App(){controller.reset();renderer.reset();if(trayVisible)Shell_NotifyIconW(NIM_DELETE,&tray);if(editFont)DeleteObject(editFont);if(editBrush)DeleteObject(editBrush);}
    void initialize() {
        scale=float(GetDpiForWindow(hwnd))/96;side=float(options.canvasSide);
        BOOL dark=TRUE;DwmSetWindowAttribute(hwnd,20,&dark,sizeof(dark));
        resizeAround(side);
        renderer=std::make_unique<Renderer>(hwnd);
        controller=std::make_unique<Controller>(options,renderer->gpu());
        localCFL=options.config.cfl;localLogRe=float(std::log10(options.config.reynolds));
        editBrush=CreateSolidBrush(RGB(26,33,45));
        editFont=CreateFontW(-int(12*scale),0,0,0,FW_NORMAL,FALSE,FALSE,FALSE,DEFAULT_CHARSET,OUT_DEFAULT_PRECIS,CLIP_DEFAULT_PRECIS,CLEARTYPE_QUALITY,DEFAULT_PITCH,L"Consolas");
        edit=CreateWindowExW(0,L"EDIT",L"2",WS_CHILD|ES_RIGHT|ES_AUTOHSCROLL,0,0,0,0,hwnd,reinterpret_cast<HMENU>(100),GetModuleHandleW(nullptr),nullptr);
        SendMessageW(edit,WM_SETFONT,reinterpret_cast<WPARAM>(editFont),TRUE);SendMessageW(edit,EM_SETLIMITTEXT,20,0);
        SetTimer(hwnd,1,16,nullptr);
        if(options.liveCheck)prepareChecks();
    }
    void resizeAround(float desired) {
        MONITORINFO info{sizeof(info)};GetMonitorInfoW(MonitorFromWindow(hwnd,MONITOR_DEFAULTTONEAREST),&info);
        RECT chrome{0,0,0,0};AdjustWindowRectExForDpi(&chrome,WS_OVERLAPPEDWINDOW,FALSE,0,UINT(scale*96));
        float extraW=focused?44.f:258.f,extraH=focused?123.f:185.f;
        float maxSide=std::min(float(info.rcWork.right-info.rcWork.left-(chrome.right-chrome.left))/scale-extraW,float(info.rcWork.bottom-info.rcWork.top-(chrome.bottom-chrome.top))/scale-extraH);
        side=std::clamp(desired,std::min(576.f,maxSide),maxSide);
        RECT current;GetWindowRect(hwnd,&current);
        int w=int(std::round((side+extraW)*scale))+int(chrome.right-chrome.left),h=int(std::round((side+extraH)*scale))+int(chrome.bottom-chrome.top);
        int x=std::clamp(current.left,info.rcWork.left,std::max(info.rcWork.left,info.rcWork.right-w));
        int y=std::clamp(current.top,info.rcWork.top,std::max(info.rcWork.top,info.rcWork.bottom-h));
        SetWindowPos(hwnd,nullptr,x,y,w,h,SWP_NOZORDER|SWP_NOACTIVATE);
    }
    void add(int id,Rect rect,const wchar_t* help,bool enabled=true,bool slider=false,bool inspector=false) {
        if(inspector){rect.top=std::max(rect.top,77.f);rect.bottom=std::min(rect.bottom,111+side);if(rect.bottom<=rect.top)return;}
        hits.push_back({id,rect,help,enabled,slider});
        if(keyboardFocus==id)renderer->rectangle({rect.left-2,rect.top-2,rect.right+2,rect.bottom+2},accent,5,true);
    }
    void button(int id,Rect r,const std::wstring& label,const wchar_t* help,bool selected=false,bool enabled=true,bool inspector=false) {
        bool hover=contains(r,mouseX,mouseY);renderer->rectangle(r,D2D1::ColorF(1,1,1,selected?.055f:hover?.07f:.02f),6);
        renderer->rectangle(r,selected?D2D1::ColorF(accent.r,accent.g,accent.b,.6f):border,6,true);
        renderer->text(label,{r.left+8,r.top,r.right-7,r.bottom},11,enabled?(selected?accent:secondary):D2D1::ColorF(.3f,.34f,.4f));
        add(id,r,help,enabled,false,inspector);
    }
    void toggle(int id,float x,float y,const wchar_t* label,bool on) {
        renderer->text(label,box(x,y,142,20),id==Regulate?10.f:11.f,foreground);
        auto track=box(x+143,y+4,25,13);renderer->rectangle(track,on?D2D1::ColorF(.26f,.60f,.55f):D2D1::ColorF(.24f,.26f,.31f),7);
        renderer->circle(x+150+(on?11:0),y+10.5f,5,D2D1::ColorF(.78f,.81f,.83f));add(id,box(x,y,168,22),label,true,false,true);
    }
    void slider(int id,float x,float y,float w,float fraction,bool enabled,const wchar_t* help) {
        renderer->line(x,y+8,x+w,y+8,D2D1::ColorF(.14f,.16f,.2f),3);
        fraction=std::clamp(fraction,0.f,1.f);if(enabled)renderer->line(x,y+8,x+w*fraction,y+8,D2D1::ColorF(.25f,.65f,.59f),3);
        renderer->circle(x+w*fraction,y+8,6,enabled?D2D1::ColorF(.59f,.60f,.62f):D2D1::ColorF(.18f,.20f,.24f));
        add(id,box(x,y-3,w,23),help,enabled,true,true);
    }
    void choice(int id,float x,float y,const wchar_t* title,const std::wstring& value,const wchar_t* help) {
        renderer->text(title,box(x,y,40,22),10,foreground,true);
        auto r=box(x+46,y,122,22);renderer->rectangle(r,D2D1::ColorF(.20f,.22f,.25f),4);
        renderer->text(value,box(x+52,y,102,22),id==Method?10.f:11.f,foreground);
        renderer->text(L"⌄",box(x+152,y,12,20),12,accent);add(id,r,help,true,false,true);
    }
    void draw(bool frameGranted=false) {
        if(done||!renderer||minimized||!IsWindowVisible(hwnd))return;
        if(!frameGranted&&nowSeconds()<nextDraw)return;
        if(!frameGranted&&WaitForSingleObject(renderer->frameSignal(),0)!=WAIT_OBJECT_0)return;
        view=controller->view();
        if(view.generation!=seenGeneration){seenGeneration=view.generation;presentations=fieldFrames=0;firstPresentation=0;lastPresented.reset();}
        if(!livePending&&dragging!=CFL)localCFL=view.config.cfl;
        if(!livePending&&dragging!=Re)localLogRe=float(std::log10(view.regulated&&view.stats.reynolds>0?view.stats.reynolds:view.config.reynolds));
        RECT cr;GetClientRect(hwnd,&cr);renderer->resize(cr.right,cr.bottom);
        float width=float(cr.right)/scale,height=float(cr.bottom)/scale;
        side=std::max(1.f,std::min(width-(focused?44:258),height-(focused?123:185)));
        hits.clear();renderer->begin(scale,true);
        renderer->circle(45,38.5f,21,D2D1::ColorF(accent.r,accent.g,accent.b,.23f),true);
        // Native vector approximation of the reference's SF hurricane symbol.
        for(float sign:{-1.f,1.f}){float px=-4,py=0;for(int i=1;i<=24;++i){float t=i/24.f,s=1-t;
            float x=-4*s*s*s-27*s*s*t-3*s*t*t+6*t*t*t,y=-21*s*s*t-36*s*t*t-12*t*t*t;
            renderer->line(45+sign*px,38.5f+sign*py,45+sign*x,38.5f+sign*y,accent,1.2f);px=x;py=y;}}
        renderer->circle(45,38.5f,5.2f,accent);renderer->circle(45,38.5f,1.5f,D2D1::ColorF(.035f,.047f,.073f));
        renderer->title(79,21,foreground);
        renderer->text(L"D E C A Y I N G   T U R B U L E N C E   I N   H D R",box(79,45,290,14),7.5f,secondary,true);
        renderer->rectangle(box(width-220,25,118,27),D2D1::ColorF(accent.r,accent.g,accent.b,.065f),14);
        renderer->circle(width-208,38,2.5f,view.ready?accent:secondary);
        renderer->text(L"CUDA / VkFFT",box(width-200,25,98,27),9,accent,true);
        button(Focus,box(width-88,23,32,32),focused?L"↙":L"↗",L"Focus mode · F");
        button(Save,box(width-46,23,32,32),L"↥",L"Save PNG · Ctrl+S",false,view.ready&&!exporting);
        renderer->rectangle(box(22,77,side,side+34),panel,15);
        renderer->rectangle(box(22,77,side,side+34),border,15,true);
        renderer->circle(36,94,2.5f,view.ready&&view.running?accent:secondary);
        const wchar_t* names[]={L"VORTICITY",L"SPEED",L"ENSTROPHY"};
        renderer->text(names[display.field],box(46,77,130,34),10,foreground,true);
        renderer->text(display.field==0?L"−ω":L"0",box(side-232,77,24,34),8,secondary,true);
        renderer->gradient(box(side-207,92,54,4),display.palette);
        renderer->text(display.field==0?L"+ω":display.field==1?L"|u|":L"ω²",box(side-148,77,24,34),8,secondary,true);
        renderer->text(std::to_wstring(view.config.n)+L"² · "+std::to_wstring(view.config.m())+L"² padded",box(side-123,77,130,34),10,secondary,true,true);
        renderer->end();
        if(view.frame)renderer->drawField(view.frame,display,22,111,side,scale);
        renderer->begin(scale,false);
        if(!view.ready){renderer->text(uiError.empty()&&view.error.empty()?L"Preparing the flow":L"Unable to start the flow",box(22+side/2-100,111+side/2-15,240,30),12,foreground);renderer->text(L"The simulation will appear here when ready.",box(22+side/2-120,111+side/2+12,270,25),10,secondary);}
        renderer->rectangle(box(36,125,143,26),D2D1::ColorF(0,0,0,.30f),13);
        renderer->text(L"UNFORCED · SEED "+std::to_wstring(view.config.seed),box(46,125,133,26),8,foreground,true);
        float transportY=111+side-49;
        renderer->rectangle(box(36,transportY,114,35),D2D1::ColorF(.06f,.07f,.1f,.75f),18);
        renderer->rectangle(box(36,transportY,114,35),D2D1::ColorF(1,1,1,.12f),18,true);
        const int ids[]={Play,Step,Reset};const wchar_t* symbols[]={view.running?L"Ⅱ":L"▶",L"▸Ⅰ",L"↶"};
        const wchar_t* helps[]={L"Pause / resume · Space",L"Single step · →",L"Reset the same initial condition · R"};
        for(int i=0;i<3;++i){auto r=box(36+38.f*i,transportY,38,35);renderer->text(symbols[i],box(r.left+12,r.top,26,35),14,foreground);add(ids[i],r,helps[i],view.ready);}
        renderer->rectangle(box(side-78,transportY+3,86,29),D2D1::ColorF(0,0,0,.30f),15);
        renderer->text(L"t  "+number(view.stats.time),box(side-68,transportY+3,74,29),10,foreground,true);
        std::string message=!uiError.empty()?uiError:view.error;
        if(!message.empty()){renderer->rectangle(box(36,transportY-65,side-28,52),D2D1::ColorF(.10f,.12f,.16f,.95f),10);renderer->text(widen(message),box(47,transportY-62,side-70,46),10,foreground);add(Dismiss,box(side-15,transportY-60,23,23),L"Dismiss message");renderer->text(L"×",box(side-15,transportY-60,23,23),15,foreground);}
        if(!focused){drawInspector();drawMetrics(width);}
        auto hit=hitAt(mouseX,mouseY);int hovered=hit?hit->id:0;
        if(hovered!=hoverId){hoverId=hovered;hoverSince=nowSeconds();}
        if(hit&&nowSeconds()-hoverSince>.6&&!dragging&&!options.guiBenchmark&&!options.liveCheck){float w=std::min(float(hit->help.size())*6+18,width-20);float x=std::clamp(mouseX,10.f,width-w-10),y=std::min(mouseY+25,height-34);renderer->rectangle(box(x,y,w,26),D2D1::ColorF(.12f,.15f,.20f,.98f),5);renderer->text(hit->help,box(x+8,y,w-16,26),10,foreground);}
        renderer->end();
        if(!options.capture.empty()&&view.ready&&view.finished)renderer->capturePNG(options.capture);
        renderer->present();
        double completed=nowSeconds();
        if(nextDraw==0)nextDraw=completed+1./60;
        else if(nextDraw<=completed)nextDraw+=(std::floor((completed-nextDraw)*60)+1)/60;
        if(view.frame){++presentations;if(view.frame!=lastPresented){++fieldFrames;lastPresented=view.frame;}lastPresentation=nowSeconds();if(firstPresentation==0)firstPresentation=lastPresentation;}
        if(options.guiBenchmark&&!measurementStarted&&firstPresentation>0&&nowSeconds()-firstPresentation>=options.guiWarmup){measurementStarted=true;controller->startMeasurement();}
        if(options.guiBenchmark&&view.finished&&view.settled&&view.frame&&reached(view.frame->time,view.stats.time))finishBenchmark();
    }
    void drawInspector() {
        float outer=22+side+18,x=outer+14,y=91-scroll;
        renderer->rectangle(box(outer,77,196,side+34),panel,15);renderer->rectangle(box(outer,77,196,side+34),border,15,true);
        renderer->clip(box(outer+1,78,194,side+32));
        choice(K0,x,y,L"k₀",std::to_wstring(view.config.k0),L"Initial wavenumber");
        renderer->text(L"Seed",box(x,y+30,40,22),10,foreground,true);
        auto seed=box(x+46,y+30,98,22);renderer->rectangle(seed,D2D1::ColorF(.10f,.13f,.18f),4);
        if(!editing)renderer->text(std::to_wstring(view.config.seed),box(x+50,y+30,90,22),11,foreground,true,true);
        else {auto visible=seed.top>=78&&seed.bottom<=110+side;ShowWindow(edit,visible?SW_SHOWNA:SW_HIDE);MoveWindow(edit,int((seed.left+3)*scale),int((seed.top+2)*scale),int(92*scale),int(18*scale),TRUE);}
        add(Seed,seed,L"Seed · Enter to apply",true,false,true);renderer->text(L"⚄",box(x+151,y+28,20,24),18,foreground);add(Dice,box(x+148,y+28,22,24),L"New seed · Ctrl+Shift+R",true,false,true);
        choice(Resolution,x,y+60,L"N",std::to_wstring(view.config.n),L"Retained resolution; nonlinear workspace is 3N/2");
        renderer->text(L"Re",box(x,y+93,22,20),10,foreground,true);
        renderer->text(std::to_wstring(uint64_t(std::llround(std::pow(10.,localLogRe)))),box(x+23,y+93,82,20),11,accent,true);
        renderer->text(view.regulated?L"AUTO":L"MANUAL",box(x+116,y+93,52,20),8,secondary,true,true);
        slider(Re,x,y+117,168,(localLogRe-3)/3,!view.regulated,L"Reynolds number · logarithmic 1,000–1,000,000");
        toggle(Regulate,x,y+143,L"Regulate with palinstrophy",view.regulated);
        renderer->text(L"10⁴P/(Z kmax²) ≈ "+number(10000*view.stats.metric,2),box(x,y+166,168,20),10,foreground,true);
        renderer->line(x,y+195,x+168,y+195,border);
        const int fields[]={Enstrophy,Vorticity,Speed};const int fieldIndex[]={2,0,1};const wchar_t* names[]={L"Enstrophy",L"Vorticity",L"Speed"};
        for(int i=0;i<3;++i){float row=y+207+i*28.f;button(fields[i],box(x,row,168,24),names[i],names[i],display.field==fieldIndex[i],true,true);renderer->circle(x+154,row+12,4.5f,display.field==fieldIndex[i]?accent:secondary,display.field!=fieldIndex[i]);}
        const int palettes[]={Inferno,Ember,Aurora,Glacier},pi[]={3,1,0,2};const wchar_t* pn[]={L"Inferno",L"Ember",L"Aurora",L"Glacier"};
        for(int i=0;i<4;++i){float row=y+299+i*29.f;button(palettes[i],box(x,row,168,24),L"",pn[i],display.palette==pi[i],true,true);renderer->text(pn[i],box(x+8,row,44,24),9,display.palette==pi[i]?foreground:secondary);renderer->gradient(box(x+58,row+6,102,12),pi[i]);}
        float contrastY=y+425;bool sigma=display.field==0&&display.colorRange==0;
        if(display.field==0){renderer->rectangle(box(x,contrastY,72,20),D2D1::ColorF(.20f,.22f,.25f),4);renderer->text(sigma?L"Sigma  ⌄":L"Exposure ⌄",box(x+5,contrastY,67,20),10,foreground);add(Range,box(x,contrastY,72,20),L"Sigma clips around the mean; Exposure adjusts soft contrast",true,false,true);}
        else renderer->text(L"Exposure",box(x,contrastY,75,20),11,foreground);
        renderer->text(sigma?L"±"+number(display.sigma,1)+L"σ":number(display.exposure,1)+L"×",box(x+90,contrastY,78,20),10,accent,true,true);
        slider(Contrast,x,contrastY+25,168,sigma?(display.sigma-1.5f)/2:(display.exposure-.25f)/2.75f,true,sigma?L"Vorticity sigma range · 1.5–3.5σ":L"Exposure · 0.25–3×");
        toggle(Flow,x,y+478,L"Velocity direction",display.flow);
        renderer->line(x,y+512,x+168,y+512,border);renderer->text(L"NUMERICS",box(x,y+522,168,16),9,secondary,true);
        const wchar_t* methodTitles[]={L"IMEX RK3",L"Integrating-factor RK3",L"ETD RK4"};
        choice(Method,x,y+547,L"Method",methodTitles[int(view.config.integrator)],L"Integrator");
        choice(Formulation,x,y+579,L"Form",view.config.form==Form::stress?L"Stress":L"Advective",L"Nonlinear form on the 3/2-padded grid");
        renderer->text(L"CFL",box(x,y+622,40,24),10,foreground,true);renderer->text(number(localCFL,2),box(x+46,y+610,122,18),11,accent,true,true);
        slider(CFL,x+46,y+633,122,(localCFL-1)/2,true,L"CFL · 1–3 · adjusts timestep without restarting");
        renderer->unclip();
        float overflow=std::max(0.f,676-side-34);if(overflow>0){float thumb=std::max(30.f,(side+6)*(side+6)/676);float travel=side+6-thumb;renderer->rectangle(box(outer+190,91+travel*(scroll/overflow),3,thumb),D2D1::ColorF(.3f,.34f,.4f,.7f),2);}
    }
    void drawMetrics(float width) {
        float top=111+side+8;renderer->rectangle(box(22,top,width-44,54),panel,10);renderer->rectangle(box(22,top,width-44,54),border,10,true);
        const wchar_t* labels[]={L"SIMULATED TIME",L"INITIAL TURNOVERS",L"KINETIC ENERGY",L"ENSTROPHY",L"TIMESTEP",L"INTEGRATION",L"STEP",L"GPU ALLOCATION"};
        std::wstring vals[]={number(view.rate),number(view.turnoversRate),number(view.stats.energy),number(view.stats.enstrophy),number(view.stats.dt),number(view.stepsRate,0),std::to_wstring(view.stats.step),number(double(view.stats.allocatedBytes+view.stats.fftBytes+renderer->allocation())/1048576,0)};
        const wchar_t* units[]={L"per wall s",L"per wall s",L"",L"",L"Δt",L"steps/s",L"",L"MiB"};
        float x=36,available=width-72,others=(available-220-14)/6;
        for(int i=0;i<8;++i){float w=i<2?110:others;
            renderer->text(labels[i],box(x,top+7,w-6,12),7,secondary,true);
            float valueWidth=i<2?63:std::min(w-8,65.f);
            renderer->text(view.ready?vals[i]:L"—",box(x,top+21,valueWidth,25),16,i<2?accent:foreground,true);
            renderer->text(units[i],box(x+valueWidth,top+29,w-valueWidth-3,15),7.5f,secondary,true);
            if(i==2&&view.history.size()>1){auto [low,high]=std::minmax_element(view.history.begin(),view.history.end());double span=std::max(*high-*low,std::max(std::abs(*high),1e-12)*.02),mid=(*high+*low)/2;
                for(size_t j=1;j<view.history.size();++j)renderer->line(x+w-31+24*float(j-1)/float(view.history.size()-1),top+33-float((view.history[j-1]-mid)/span)*13.6f,x+w-31+24*float(j)/float(view.history.size()-1),top+33-float((view.history[j]-mid)/span)*13.6f,accent,1.1f);}
            x+=w;if(i<7){renderer->line(x-4,top+14,x-4,top+40,border);}
        }
    }
    Hit* hitAt(float x,float y){for(auto& h:hits)if(contains(h.rect,x,y))return &h;return nullptr;}
    void flush(){if(!livePending)return;controller->live(localCFL,std::pow(10.,localLogRe));lastLive=nowSeconds();livePending=false;}
    void editSeed() {auto h=std::find_if(hits.begin(),hits.end(),[](const Hit& h){return h.id==Seed;});if(h==hits.end())return;editing=true;SetWindowTextW(edit,std::to_wstring(view.config.seed).c_str());ShowWindow(edit,SW_SHOW);SetFocus(edit);SendMessageW(edit,EM_SETSEL,0,-1);draw();}
    void commitSeed(bool apply) {if(!editing)return;wchar_t value[32];GetWindowTextW(edit,value,32);editing=false;ShowWindow(edit,SW_HIDE);SetFocus(hwnd);
        if(apply){try{std::wstring s=value;if(s.empty()||s.find_first_not_of(L"0123456789")!=std::wstring::npos)return;uint64_t seed=std::stoull(s);if(seed!=view.config.seed){auto c=view.config;c.seed=seed;controller->configure(c);}}catch(const std::exception&) {}}
    }
    int popup(const std::vector<std::wstring>& choices,int current,Rect rect) {
        HMENU menu=CreatePopupMenu();for(size_t i=0;i<choices.size();++i)AppendMenuW(menu,MF_STRING|(int(i)==current?MF_CHECKED:0),i+1,choices[i].c_str());
        POINT p{LONG(rect.left*scale),LONG(rect.bottom*scale)};ClientToScreen(hwnd,&p);SetForegroundWindow(hwnd);SetFocus(hwnd);
        std::thread menuInput;
        if(automatedMenuSelection>=0){int selected=std::exchange(automatedMenuSelection,-1);HWND owner=hwnd;
            menuInput=std::thread([owner,menu,selected]{Sleep(120);if(GetForegroundWindow()!=owner){PostMessageW(owner,WM_CANCELMODE,0,0);return;}
                RECT item{};if(!GetMenuItemRect(owner,menu,UINT(selected),&item)){PostMessageW(owner,WM_CANCELMODE,0,0);return;}
                POINT previous;GetCursorPos(&previous);SetCursorPos((item.left+item.right)/2,(item.top+item.bottom)/2);
                INPUT clicks[2]{};clicks[0].type=clicks[1].type=INPUT_MOUSE;clicks[0].mi.dwFlags=MOUSEEVENTF_LEFTDOWN;clicks[1].mi.dwFlags=MOUSEEVENTF_LEFTUP;SendInput(2,clicks,sizeof(INPUT));
                Sleep(40);if(GetForegroundWindow()==owner)SetCursorPos(previous.x,previous.y);});}
        int result=TrackPopupMenu(menu,TPM_RETURNCMD|TPM_NONOTIFY|TPM_LEFTALIGN,p.x,p.y,0,hwnd,nullptr);if(menuInput.joinable())menuInput.join();DestroyMenu(menu);return result?result-1:-1;
    }
    void activate(int id) {
        if(!controller)return;flush();view=controller->view();auto c=view.config;
        auto found=std::find_if(hits.begin(),hits.end(),[&](const Hit& h){return h.id==id;});Rect r=found!=hits.end()?found->rect:box(40+side,100,100,22);
        switch(id) {
        case Focus:commitSeed(true);focused=!focused;scroll=0;resizeAround(side);break;
        case Save:save();break;
        case Play:if(view.ready)controller->running(!view.running);break;
        case Step:if(view.ready)controller->singleStep();break;
        case Reset:if(view.ready)controller->reset();break;
        case Seed:editSeed();break;
        case Dice:++c.seed;controller->configure(c);break;
        case K0:{int vals[]={2,4,6,8,10,12,16,20};std::vector<std::wstring> labels;int cur=0;for(int i=0;i<8;++i){labels.push_back(std::to_wstring(vals[i]));if(vals[i]==c.k0)cur=i;}int k=popup(labels,cur,r);if(k>=0&&c.k0!=vals[k]){c.k0=vals[k];controller->configure(c);}break;}
        case Resolution:{int vals[]={128,256,512,1024,2048,4096};std::vector<std::wstring> labels;int cur=0;for(int i=0;i<6;++i){labels.push_back(std::to_wstring(vals[i]));if(vals[i]==c.n)cur=i;}int k=popup(labels,cur,r);if(k>=0&&c.n!=vals[k]){c.n=vals[k];controller->configure(c);}break;}
        case Regulate:controller->regulate(!view.regulated);break;
        case Enstrophy:display.field=2;break;case Vorticity:display.field=0;break;case Speed:display.field=1;break;
        case Inferno:display.palette=3;break;case Ember:display.palette=1;break;case Aurora:display.palette=0;break;case Glacier:display.palette=2;break;
        case Range:{int k=popup({L"Sigma",L"Exposure"},display.colorRange,r);if(k>=0)display.colorRange=k;break;}
        case Flow:display.flow=!display.flow;break;
        case Method:{int k=popup({L"IMEX RK3",L"Integrating-factor RK3",L"ETD RK4"},int(c.integrator),r);if(k>=0&&k!=int(c.integrator)){c.integrator=Integrator(k);if(k!=2)c.cfl=std::min(c.cfl,1.5f);controller->configure(c);}break;}
        case Formulation:{int k=popup({L"Stress",L"Advective"},int(c.form),r);if(k>=0&&k!=int(c.form)){c.form=Form(k);controller->configure(c);}break;}
        case Dismiss:uiError.clear();controller->dismissError();break;
        }
        controller->velocity(display.velocity());
    }
    void drag(int id,float x){auto h=std::find_if(hits.begin(),hits.end(),[&](const Hit& h){return h.id==id;});if(h==hits.end()||!h->enabled)return;
        float f=std::clamp((x-h->rect.left)/(h->rect.right-h->rect.left),0.f,1.f);
        if(id==CFL){localCFL=1+2*f;livePending=true;}else if(id==Re){localLogRe=3+3*f;livePending=true;}
        else if(id==Contrast){if(display.field==0&&display.colorRange==0)display.sigma=1.5f+2*f;else display.exposure=.25f+2.75f*f;}
        if(livePending&&nowSeconds()-lastLive>=1./30)flush();
    }
    void save() {
        if(!view.frame||exporting)return;
        wchar_t path[32768];swprintf_s(path,L"2DFastTurbo-%06llu.png",view.frame->sequence);
        if(!automatedSave.empty())wcscpy_s(path,automatedSave.c_str());
        OPENFILENAMEW dialog{sizeof(dialog)};dialog.hwndOwner=hwnd;dialog.lpstrFilter=L"PNG image\0*.png\0\0";dialog.lpstrFile=path;dialog.nMaxFile=32768;dialog.lpstrDefExt=L"png";dialog.Flags=OFN_OVERWRITEPROMPT|OFN_PATHMUSTEXIST|OFN_NOCHANGEDIR;
        if(!automatedSave.empty()){
            dialog.Flags|=OFN_EXPLORER|OFN_ENABLEHOOK;
            dialog.lpfnHook=[](HWND h,UINT m,WPARAM,LPARAM)->UINT_PTR{if(m==WM_INITDIALOG)PostMessageW(GetParent(h),WM_COMMAND,IDOK,0);return 0;};
        }
        if(GetSaveFileNameW(&dialog)){exporting=true;try{renderer->exportPNG(view.frame,display,path);}catch(const std::exception& e){uiError=e.what();}exporting=false;}
    }
    void hideToTray(){commitSeed(true);ShowWindow(hwnd,SW_HIDE);controller->visible(false);tray={};tray.cbSize=sizeof(tray);tray.hWnd=hwnd;tray.uID=1;tray.uFlags=NIF_MESSAGE|NIF_ICON|NIF_TIP;tray.uCallbackMessage=WM_APP+1;tray.hIcon=LoadIconW(GetModuleHandleW(nullptr),MAKEINTRESOURCEW(1));wcscpy_s(tray.szTip,L"2D FAST TURBO · simulation continues");trayVisible=Shell_NotifyIconW(NIM_ADD,&tray)!=FALSE;if(!trayVisible)show();}
    void show(){ShowWindow(hwnd,SW_RESTORE);SetForegroundWindow(hwnd);controller->visible(true);if(trayVisible){Shell_NotifyIconW(NIM_DELETE,&tray);trayVisible=false;}}
    void finishBenchmark(){
        if(!options.renderCheck.empty()){
            std::filesystem::create_directories(options.renderCheck);
            std::ofstream manifest(options.renderCheck/L"manifest.json");manifest<<"[";bool first=true;
            for(int palette=0;palette<4;++palette)for(int field=0;field<3;++field)for(int range=0;range<(field==0?2:1);++range)for(int extreme=0;extreme<2;++extreme){
                Display d;d.palette=palette;d.field=field;d.colorRange=range;d.sigma=extreme?3.5f:1.5f;d.exposure=extreme?3.f:.25f;
                std::string name="p"+std::to_string(palette)+"-f"+std::to_string(field)+"-r"+std::to_string(range)+"-e"+std::to_string(extreme)+".png";
                renderer->exportPNG(view.frame,d,options.renderCheck/name,256);
                if(!first)manifest<<',';first=false;manifest<<"{\"file\":\""<<name<<"\",\"palette\":"<<palette<<",\"field\":"<<field<<",\"range\":"<<range<<",\"sigma\":"<<d.sigma<<",\"exposure\":"<<d.exposure<<"}";
            }
            manifest<<"]\n";
        }
        if(!options.exportImage.empty())renderer->exportPNG(view.frame,display,options.exportImage);
        double fps=presentations>1&&lastPresentation>firstPresentation?(presentations-1)/(lastPresentation-firstPresentation):0;
        auto json=reportJSON(options,view.initial,view.stats,view.wall,view.gpuSeconds,view.batches,view.snapshots,"gui-benchmark",fps);
        json.pop_back();json.pop_back();std::ostringstream extra;extra<<",\"rendering\":{\"cuda_d3d11_interop\":true,\"hdr\":"<<(renderer->isHDR()?"true":"false")<<",\"canvas_pixels\":"<<side*scale<<",\"presentations\":"<<presentations<<",\"distinct_field_frames\":"<<fieldFrames<<"},\"passed\":true}\n";json+=extra.str();writeReport(options,json);done=true;
    }
    void prepareChecks();
    void tickChecks();
    LRESULT message(UINT m,WPARAM w,LPARAM l) {
        switch(m) {
        case WM_ERASEBKGND:return 1;
        case WM_PAINT:{PAINTSTRUCT ps;BeginPaint(hwnd,&ps);EndPaint(hwnd,&ps);return 0;}
        case WM_TIMER:if(livePending&&nowSeconds()-lastLive>=1./30)flush();draw();if(options.liveCheck)tickChecks();return 0;
        case WM_SIZE:minimized=w==SIZE_MINIMIZED;if(controller)controller->visible(!minimized&&IsWindowVisible(hwnd));return 0;
        case WM_SIZING:{RECT* r=reinterpret_cast<RECT*>(l);RECT old;GetWindowRect(hwnd,&old);RECT chrome{0,0,0,0};AdjustWindowRectExForDpi(&chrome,WS_OVERLAPPEDWINDOW,FALSE,0,UINT(scale*96));float ew=(focused?44.f:258.f)*scale+chrome.right-chrome.left,eh=(focused?123.f:185.f)*scale+chrome.bottom-chrome.top;
            float dw=std::abs(float(r->right-r->left-(old.right-old.left))),dh=std::abs(float(r->bottom-r->top-(old.bottom-old.top)));
            float s=std::max(std::min(576.f,side)*scale,dw>=dh?float(r->right-r->left)-ew:float(r->bottom-r->top)-eh);
            if(w==WMSZ_LEFT||w==WMSZ_TOPLEFT||w==WMSZ_BOTTOMLEFT)r->left=r->right-int(s+ew);else r->right=r->left+int(s+ew);
            if(w==WMSZ_TOP||w==WMSZ_TOPLEFT||w==WMSZ_TOPRIGHT)r->top=r->bottom-int(s+eh);else r->bottom=r->top+int(s+eh);return TRUE;}
        case WM_EXITSIZEMOVE:resizeAround(side);return 0;
        case WM_DPICHANGED:scale=float(HIWORD(w))/96;resizeAround(side);return 0;
        case WM_MOUSEMOVE:mouseX=float(GET_X_LPARAM(l))/scale;mouseY=float(GET_Y_LPARAM(l))/scale;if(dragging)drag(dragging,mouseX);return 0;
        case WM_LBUTTONDOWN:{mouseX=float(GET_X_LPARAM(l))/scale;mouseY=float(GET_Y_LPARAM(l))/scale;auto hit=hitAt(mouseX,mouseY);if(hit&&hit->enabled){int id=hit->id;bool slider=hit->slider;keyboardFocus=id;if(editing&&id!=Seed)commitSeed(true);if(slider){dragging=id;SetCapture(hwnd);drag(id,mouseX);}else activate(id);}return 0;}
        case WM_LBUTTONUP:if(dragging){drag(dragging,float(GET_X_LPARAM(l))/scale);dragging=0;ReleaseCapture();flush();}return 0;
        case WM_CAPTURECHANGED:dragging=0;flush();return 0;
        case WM_MOUSEWHEEL:if(!focused){POINT p{GET_X_LPARAM(l),GET_Y_LPARAM(l)};ScreenToClient(hwnd,&p);if(float(p.x)/scale>side+40){scroll=std::clamp(scroll-float(GET_WHEEL_DELTA_WPARAM(w))/120*36,0.f,std::max(0.f,676-side-34));commitSeed(true);}}return 0;
        case WM_CTLCOLOREDIT:SetTextColor(reinterpret_cast<HDC>(w),RGB(230,230,230));SetBkColor(reinterpret_cast<HDC>(w),RGB(26,33,45));return reinterpret_cast<LRESULT>(editBrush);
        case WM_CLOSE:if(options.guiBenchmark||options.liveCheck){done=true;exitCode=2;}else hideToTray();return 0;
        case WM_DESTROY:done=true;return 0;
        case WM_APP+1:if(l==WM_LBUTTONUP||l==WM_LBUTTONDBLCLK)show();else if(l==WM_RBUTTONUP){HMENU menu=CreatePopupMenu();AppendMenuW(menu,MF_STRING,1,L"Show Simulation");AppendMenuW(menu,MF_STRING,2,L"Quit 2DFastTurbo");POINT p;GetCursorPos(&p);SetForegroundWindow(hwnd);int choice=TrackPopupMenu(menu,TPM_RETURNCMD,p.x,p.y,0,hwnd,nullptr);DestroyMenu(menu);if(choice==1)show();if(choice==2)done=true;}return 0;
        }
        return DefWindowProcW(hwnd,m,w,l);
    }
    bool key(const MSG& msg) {
        if(msg.message!=WM_KEYDOWN)return false;
        bool ctrl=(GetKeyState(VK_CONTROL)&0x8000)!=0,shift=(GetKeyState(VK_SHIFT)&0x8000)!=0,repeat=(msg.lParam&(1LL<<30))!=0;
        if(msg.wParam==VK_SPACE&&!ctrl&&!shift){if(!repeat)activate(Play);return true;}
        if(editing){if(msg.wParam==VK_RETURN){commitSeed(true);return true;}if(msg.wParam==VK_ESCAPE){commitSeed(false);return true;}if(msg.wParam==VK_TAB){commitSeed(true);return true;}return false;}
        if(repeat)return false;
        if(ctrl&&msg.wParam=='Q'){done=true;return true;}
        if(ctrl&&msg.wParam=='S'){activate(Save);return true;}
        if(ctrl&&shift&&msg.wParam=='R'){activate(Dice);return true;}
        if(!ctrl&&msg.wParam=='R'){activate(Reset);return true;}
        if(!ctrl&&msg.wParam=='F'){activate(Focus);return true;}
        if(!ctrl&&msg.wParam==VK_RIGHT){activate(Step);return true;}
        if(msg.wParam==VK_TAB){std::vector<int> ids;for(auto h:hits)if(h.enabled)ids.push_back(h.id);if(!ids.empty()){auto it=std::find(ids.begin(),ids.end(),keyboardFocus);int i=it==ids.end()?-1:int(it-ids.begin());keyboardFocus=ids[(i+(shift?int(ids.size())-1:1)+int(ids.size()))%ids.size()];}return true;}
        if(msg.wParam==VK_RETURN&&keyboardFocus){activate(keyboardFocus);return true;}
        if((msg.wParam==VK_UP||msg.wParam==VK_DOWN)&&keyboardFocus){auto it=std::find_if(hits.begin(),hits.end(),[&](const Hit& h){return h.id==keyboardFocus;});if(it!=hits.end()&&it->slider){float f=keyboardFocus==CFL?(localCFL-1)/2:keyboardFocus==Re?(localLogRe-3)/3:display.field==0&&display.colorRange==0?(display.sigma-1.5f)/2:(display.exposure-.25f)/2.75f;f+=(msg.wParam==VK_UP?.01f:-.01f);drag(keyboardFocus,it->rect.left+f*(it->rect.right-it->rect.left));flush();return true;}}
        return false;
    }
};
void App::prepareChecks() {
    auto wait=[&](std::string name,std::function<bool()> body){checks.push_back({std::move(name),std::move(body),0});};
    auto once=[&](std::string name,std::function<void()> body){auto first=std::make_shared<bool>(true);wait(std::move(name),[body=std::move(body),first]{if(*first){body();*first=false;}return true;});};
    auto assertThat=[](bool ok,const char* why){if(!ok)throw std::runtime_error(why);};
    wait("visible_first_frame",[this]{return view.ready&&view.stats.step>0&&view.rate>0&&presentations>0;});
    once("pause_button",[this]{testRate=view.rate;activate(Play);});
    wait("pause_drained",[this]{if(!view.settled||view.running)return false;testStep=view.stats.step;testTime=nowSeconds();return true;});
    wait("pause_stable_and_throughput_retained",[this,assertThat]{if(nowSeconds()-testTime<.15)return false;assertThat(view.stats.step==testStep&&view.rate==testRate,"Pause changed state or displayed throughput");return true;});
    once("single_step_button",[this]{activate(Step);});
    wait("single_step_exactly_one",[this,assertThat]{if(!view.settled||view.stats.step==testStep)return false;assertThat(view.stats.step==testStep+1&&view.rate==testRate,"Single step count or throughput incorrect");testGeneration=view.generation;testStep=view.stats.step;return true;});
    once("manual_reynolds_toggle",[this]{if(view.regulated)activate(Regulate);});
    once("live_cfl_reynolds_sliders",[this]{localCFL=1;localLogRe=3;livePending=true;flush();testTime=nowSeconds();});
    wait("paused_live_edits_preserve_frame_clock",[this,assertThat]{if(nowSeconds()-testTime<.1)return false;assertThat(view.generation==testGeneration&&view.stats.step==testStep&&view.stats.reynolds==1000,"Live controls reset the paused flow");return true;});
    for(int field:{Enstrophy,Vorticity,Speed})once("field_"+std::to_string(field),[this,field]{activate(field);});
    for(int palette:{Inferno,Ember,Aurora,Glacier})once("palette_"+std::to_string(palette),[this,palette]{activate(palette);});
    once("flow_overlay_on",[this]{activate(Flow);});
    wait("velocity_snapshot",[this]{return view.frame&&view.frame->hasVelocity;});
    once("flow_overlay_off",[this]{activate(Flow);activate(Vorticity);activate(Ember);});
    auto select=[this](int id,int index){automatedMenuSelection=index;activate(id);};
    once("exposure_menu",[select]{select(Range,1);});
    once("exposure_slider",[this,assertThat]{assertThat(display.colorRange==1,"Color-range popup did not select Exposure");auto h=std::find_if(hits.begin(),hits.end(),[](auto h){return h.id==Contrast;});if(h==hits.end())throw std::runtime_error("Missing contrast slider");drag(Contrast,h->rect.right);assertThat(display.exposure==3,"Exposure upper limit");});
    once("sigma_menu",[select]{select(Range,0);});
    once("sigma_slider_and_retained_exposure",[this,assertThat]{assertThat(display.colorRange==0&&display.exposure==3,"Independent contrast values");auto h=std::find_if(hits.begin(),hits.end(),[](auto h){return h.id==Contrast;});drag(Contrast,h->rect.left);assertThat(display.sigma==1.5f,"Sigma lower limit");display.exposure=1.2f;display.sigma=2.5f;});
    once("focus_button",[this]{testSide=side;activate(Focus);});
    once("focus_preserves_square",[this,assertThat]{assertThat(focused&&std::abs(side-testSide)<=1,"Focus changed canvas size");activate(Focus);});
    once("controls_restored",[this,assertThat]{assertThat(!focused&&std::abs(side-testSide)<=1,"Restoring controls changed canvas size");});
    once("seed_edit",[this]{testConfig=view.config;activate(Seed);SetWindowTextW(edit,L"17");commitSeed(true);});
    wait("seed_committed_and_reset_paused",[this]{return view.ready&&view.settled&&view.config.seed==17&&view.stats.step==0;});
    once("next_seed_button",[this]{activate(Dice);});
    wait("next_seed_applied",[this]{return view.ready&&view.settled&&view.config.seed==18&&view.stats.step==0;});
    once("wavenumber_menu",[select]{select(K0,2);});
    wait("wavenumber_applied",[this]{return view.ready&&view.settled&&view.config.k0==6;});
    once("resolution_menu",[select]{select(Resolution,1);});
    wait("resolution_retained_padding",[this,assertThat]{if(!view.ready||!view.settled||view.config.n!=256)return false;assertThat(view.config.m()==384&&view.frame->n==256,"N/M semantics");return true;});
    for(int method=0;method<3;++method){
        once("method_menu_"+std::to_string(method),[select,method]{select(Method,method);});
        wait("method_applied_"+std::to_string(method),[this,method]{return view.ready&&view.settled&&int(view.config.integrator)==method;});
    }
    once("advective_menu",[select]{select(Formulation,1);});
    wait("advective_applied",[this]{return view.ready&&view.settled&&view.config.form==Form::advective;});
    once("stress_menu",[select]{select(Formulation,0);});
    wait("stress_applied",[this]{return view.ready&&view.settled&&view.config.form==Form::stress;});
    once("restore_initial_configuration",[this]{controller->configure(testConfig);});
    wait("restored_configuration_ready",[this]{return view.ready&&view.settled&&view.config.n==testConfig.n&&view.config.seed==testConfig.seed;});
    once("regulation_toggle_on",[this]{if(!view.regulated)activate(Regulate);});
    once("re_slider_disabled",[this,assertThat]{auto h=std::find_if(hits.begin(),hits.end(),[](auto h){return h.id==Re;});assertThat(h!=hits.end()&&!h->enabled,"Re slider must be disabled during regulation");});
    once("reset_button",[this]{activate(Reset);});
    wait("reset_zero_time_and_throughput",[this,assertThat]{if(!view.ready||!view.settled)return false;assertThat(view.stats.step==0&&view.stats.time==0&&view.rate==0,"Reset did not clear clock and throughput");return true;});
    once("minimize",[this]{ShowWindow(hwnd,SW_MINIMIZE);testTime=nowSeconds();});
    wait("restore_window",[this]{if(nowSeconds()-testTime<.1)return false;show();return true;});
    once("close_to_tray",[this]{hideToTray();testTime=nowSeconds();});
    wait("reopen_same_state",[this,assertThat]{if(nowSeconds()-testTime<.1)return false;show();assertThat(controller->view().stats.step==0,"Reopen reset or advanced paused state");return true;});
    once("space_keyboard_transport",[this]{MSG key{};key.message=WM_KEYDOWN;key.wParam=VK_SPACE;this->key(key);key.lParam=1LL<<30;this->key(key);});
    wait("space_repeat_ignored",[this]{return view.running&&view.stats.step>0;});
    once("pause_final",[this]{activate(Play);});
    wait("final_settled",[this]{return view.settled&&!view.running;});
    if(!options.exportImage.empty())once("native_save_png_dialog",[this,assertThat]{
        automatedSave=std::filesystem::absolute(options.exportImage).parent_path()/(L"native-dialog-"+std::to_wstring(GetTickCount64())+L".png");
        activate(Save);assertThat(std::filesystem::exists(automatedSave)&&std::filesystem::file_size(automatedSave)>1024,"Native Save PNG did not write an image");automatedSave.clear();
    });
}
void App::tickChecks() {
    if(done||!controller||checking)return;
    checking=true;
    struct ClearChecking { bool& flag; ~ClearChecking(){flag=false;} } clear{checking};
    if(!view.error.empty()||!uiError.empty())throw std::runtime_error("GUI check failed: "+view.error+uiError);
    if(checks.empty()) {
        if(!options.exportImage.empty())renderer->exportPNG(view.frame,display,options.exportImage);
        if(!options.capture.empty())renderer->capturePNG(options.capture);
        std::ostringstream json;json<<"{\"mode\":\"live-check\",\"passed\":true,\"checks\":[";
        for(size_t i=0;i<passed.size();++i){if(i)json<<',';json<<'"'<<passed[i]<<'"';}json<<"],\"cuda_d3d11_interop\":true}\n";writeReport(options,json.str());done=true;return;
    }
    auto& check=checks.front();if(check.started==0)check.started=nowSeconds();
    if(nowSeconds()-check.started>120)throw std::runtime_error("GUI check timed out: "+check.name);
    if(check.action()){passed.push_back(check.name);std::cerr<<"PASS GUI "<<check.name<<'\n';checks.pop_front();}
}
LRESULT CALLBACK windowProc(HWND h,UINT m,WPARAM w,LPARAM l) {
    App* app=reinterpret_cast<App*>(GetWindowLongPtrW(h,GWLP_USERDATA));
    if(m==WM_NCCREATE){app=static_cast<App*>(reinterpret_cast<CREATESTRUCTW*>(l)->lpCreateParams);app->hwnd=h;SetWindowLongPtrW(h,GWLP_USERDATA,reinterpret_cast<LONG_PTR>(app));}
    if(app){try{return app->message(m,w,l);}catch(const std::exception& e){app->uiError=e.what();if(app->options.guiBenchmark||app->options.liveCheck){std::cerr<<"GUI: "<<e.what()<<'\n';app->exitCode=2;app->done=true;}return 0;}}
    return DefWindowProcW(h,m,w,l);
}
}
int runApp(const Options& options) {
    // Keep the 60 Hz deadline accurate on Windows' default 15.6 ms timer tick.
    // DXGI still gates presentation; this only improves the CPU deadline wait.
    struct TimerResolution {
        bool active=timeBeginPeriod(1)==TIMERR_NOERROR;
        ~TimerResolution(){if(active)timeEndPeriod(1);}
    } timerResolution;
    SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
    hrCheck(CoInitializeEx(nullptr,COINIT_APARTMENTTHREADED),"Initialize COM");
    int code=0;
    {App app(options);WNDCLASSEXW wc{sizeof(wc)};wc.style=CS_HREDRAW|CS_VREDRAW;wc.lpfnWndProc=windowProc;wc.hInstance=GetModuleHandleW(nullptr);wc.hCursor=LoadCursorW(nullptr,IDC_ARROW);wc.hIcon=LoadIconW(wc.hInstance,MAKEINTRESOURCEW(1));wc.lpszClassName=L"FastTurboWindows";RegisterClassExW(&wc);
        HWND hwnd=CreateWindowExW(0,wc.lpszClassName,L"2DFastTurbo",WS_OVERLAPPEDWINDOW,CW_USEDEFAULT,CW_USEDEFAULT,900,900,nullptr,nullptr,wc.hInstance,&app);
        if(!hwnd)throw std::runtime_error("Cannot create simulation window.");
        ShowWindow(hwnd,SW_SHOW);UpdateWindow(hwnd);app.initialize();
        MSG msg{};while(!app.done){
            double remaining=std::max(0.,app.nextDraw-nowSeconds());
            bool visible=!app.minimized&&IsWindowVisible(hwnd)&&remaining==0;HANDLE pacing=app.renderer->frameSignal();
            DWORD timeout=remaining>0?DWORD(std::max(1.,std::ceil(remaining*1000))):50;
            DWORD event=MsgWaitForMultipleObjectsEx(visible?1:0,visible?&pacing:nullptr,timeout,QS_ALLINPUT,MWMO_INPUTAVAILABLE);
            if(visible&&event==WAIT_OBJECT_0){try{app.draw(true);}catch(const std::exception& e){app.uiError=e.what();if(options.guiBenchmark||options.liveCheck){std::cerr<<e.what()<<'\n';app.done=true;app.exitCode=2;}}}
            while(!app.done&&PeekMessageW(&msg,nullptr,0,0,PM_REMOVE)){if(msg.message==WM_QUIT){app.done=true;break;}if(!app.key(msg)){TranslateMessage(&msg);DispatchMessageW(&msg);}}
        }
        KillTimer(hwnd,1);code=app.exitCode;DestroyWindow(hwnd);
    }
    CoUninitialize();return code;
}
}
