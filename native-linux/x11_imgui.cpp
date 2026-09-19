#include "x11_imgui.hpp"
#include "imgui.h"
#include <X11/Xlib.h>
#include <X11/Xutil.h>
#include <X11/Xatom.h>
#include <X11/XKBlib.h>
#include <X11/keysym.h>
#include <X11/cursorfont.h>
#include <array>
#include <chrono>
#include <clocale>
#include <cfloat>
#include <string>
#include <vector>
#include <unistd.h>
#include <poll.h>
#include <algorithm>
#include <cerrno>
#include <cmath>

namespace {
ImGuiKey key(KeySym k) {
    if(k>=XK_a&&k<=XK_z)return ImGuiKey(ImGuiKey_A+k-XK_a);
    if(k>=XK_A&&k<=XK_Z)return ImGuiKey(ImGuiKey_A+k-XK_A);
    if(k>=XK_0&&k<=XK_9)return ImGuiKey(ImGuiKey_0+k-XK_0);
    if(k>=XK_F1&&k<=XK_F12)return ImGuiKey(ImGuiKey_F1+k-XK_F1);
    switch(k){
    case XK_Tab:case XK_ISO_Left_Tab:return ImGuiKey_Tab;
    case XK_Left:return ImGuiKey_LeftArrow;case XK_Right:return ImGuiKey_RightArrow;
    case XK_Up:return ImGuiKey_UpArrow;case XK_Down:return ImGuiKey_DownArrow;
    case XK_Page_Up:return ImGuiKey_PageUp;case XK_Page_Down:return ImGuiKey_PageDown;
    case XK_Home:return ImGuiKey_Home;case XK_End:return ImGuiKey_End;
    case XK_Insert:return ImGuiKey_Insert;case XK_Delete:return ImGuiKey_Delete;
    case XK_BackSpace:return ImGuiKey_Backspace;case XK_space:return ImGuiKey_Space;
    case XK_Return:case XK_KP_Enter:return ImGuiKey_Enter;case XK_Escape:return ImGuiKey_Escape;
    case XK_Control_L:return ImGuiKey_LeftCtrl;case XK_Control_R:return ImGuiKey_RightCtrl;
    case XK_Shift_L:return ImGuiKey_LeftShift;case XK_Shift_R:return ImGuiKey_RightShift;
    case XK_Alt_L:return ImGuiKey_LeftAlt;case XK_Alt_R:return ImGuiKey_RightAlt;
    case XK_Super_L:return ImGuiKey_LeftSuper;case XK_Super_R:return ImGuiKey_RightSuper;
    case XK_minus:return ImGuiKey_Minus;case XK_equal:return ImGuiKey_Equal;
    case XK_comma:return ImGuiKey_Comma;case XK_period:return ImGuiKey_Period;
    case XK_slash:return ImGuiKey_Slash;case XK_semicolon:return ImGuiKey_Semicolon;
    case XK_apostrophe:return ImGuiKey_Apostrophe;case XK_grave:return ImGuiKey_GraveAccent;
    case XK_bracketleft:return ImGuiKey_LeftBracket;case XK_bracketright:return ImGuiKey_RightBracket;
    case XK_backslash:return ImGuiKey_Backslash;default:return ImGuiKey_None;
    }
}
}
struct X11ImGui::Impl {
    Display* x;
    Window window;
    Atom protocols,deleteWindow,clipboard,utf8,targets,property;
    XIM im=nullptr;
    XIC ic=nullptr;
    std::string copied,pasted;
    std::array<Cursor,ImGuiMouseCursor_COUNT> cursors{};
    double last=0;
    int lastCursor=-2;
    static Impl& self(){return *static_cast<Impl*>(ImGui::GetPlatformIO().Platform_ClipboardUserData);}
    void selection(const XSelectionRequestEvent& request){
        XEvent reply{};reply.xselection.type=SelectionNotify;reply.xselection.display=x;
        reply.xselection.requestor=request.requestor;reply.xselection.selection=request.selection;
        reply.xselection.target=request.target;reply.xselection.time=request.time;
        reply.xselection.property=None;
        Atom dest=request.property?request.property:request.target;
        if(request.target==targets){
            Atom available[]={targets,utf8,XA_STRING};
            XChangeProperty(x,request.requestor,dest,XA_ATOM,32,PropModeReplace,
                            reinterpret_cast<unsigned char*>(available),3);
            reply.xselection.property=dest;
        }else if(request.target==utf8||request.target==XA_STRING){
            XChangeProperty(x,request.requestor,dest,request.target,8,PropModeReplace,
                            reinterpret_cast<const unsigned char*>(copied.data()),int(copied.size()));
            reply.xselection.property=dest;
        }
        XSendEvent(x,request.requestor,False,0,&reply);XFlush(x);
    }
};

X11ImGui::X11ImGui(void* display,uintptr_t window):impl(std::make_unique<Impl>()){
    auto& p=*impl;p.x=static_cast<Display*>(display);p.window=Window(window);
    p.protocols=XInternAtom(p.x,"WM_PROTOCOLS",False);
    p.deleteWindow=XInternAtom(p.x,"WM_DELETE_WINDOW",False);
    p.clipboard=XInternAtom(p.x,"CLIPBOARD",False);p.utf8=XInternAtom(p.x,"UTF8_STRING",False);
    p.targets=XInternAtom(p.x,"TARGETS",False);p.property=XInternAtom(p.x,"FASTTURBO_CLIPBOARD",False);
    XSetWMProtocols(p.x,p.window,&p.deleteWindow,1);
    XSizeHints hints{};hints.flags=PMinSize;hints.min_width=760;hints.min_height=680;
    XSetWMNormalHints(p.x,p.window,&hints);
    XClassHint cls{};cls.res_name=const_cast<char*>("fastturbo-linux");cls.res_class=const_cast<char*>("FastTurbo");
    XSetClassHint(p.x,p.window,&cls);
    unsigned long pid=static_cast<unsigned long>(getpid());
    XChangeProperty(p.x,p.window,XInternAtom(p.x,"_NET_WM_PID",False),XA_CARDINAL,32,
                    PropModeReplace,reinterpret_cast<unsigned char*>(&pid),1);
    Bool supported=False;XkbSetDetectableAutoRepeat(p.x,True,&supported);
    std::setlocale(LC_CTYPE,"");XSetLocaleModifiers("");
    p.im=XOpenIM(p.x,nullptr,nullptr,nullptr);
    if(p.im)p.ic=XCreateIC(p.im,XNInputStyle,XIMPreeditNothing|XIMStatusNothing,
                          XNClientWindow,p.window,XNFocusWindow,p.window,nullptr);
    const unsigned shapes[]={XC_left_ptr,XC_xterm,XC_fleur,XC_sb_v_double_arrow,XC_sb_h_double_arrow,
                             XC_bottom_left_corner,XC_bottom_right_corner,XC_hand2,XC_pirate};
    for(size_t i=0;i<p.cursors.size();++i)p.cursors[i]=XCreateFontCursor(p.x,shapes[i<9?i:0]);
    auto& io=ImGui::GetIO();io.BackendPlatformName="fastturbo_x11";
    io.BackendFlags|=ImGuiBackendFlags_HasMouseCursors;
    ImGui::GetMainViewport()->PlatformHandleRaw=reinterpret_cast<void*>(window);
    auto& platform=ImGui::GetPlatformIO();platform.Platform_ClipboardUserData=&p;
    platform.Platform_SetClipboardTextFn=[](ImGuiContext*,const char* text){
        auto& s=Impl::self();s.copied=text;XSetSelectionOwner(s.x,s.clipboard,s.window,CurrentTime);XFlush(s.x);
    };
    platform.Platform_GetClipboardTextFn=[](ImGuiContext*)->const char*{
        auto& s=Impl::self();
        if(XGetSelectionOwner(s.x,s.clipboard)==s.window)return s.copied.c_str();
        s.pasted.clear();XDeleteProperty(s.x,s.window,s.property);
        XConvertSelection(s.x,s.clipboard,s.utf8,s.property,s.window,CurrentTime);XFlush(s.x);
        // Clipboard is a user request, never part of the simulation/render loop.
        auto deadline=std::chrono::steady_clock::now()+std::chrono::milliseconds(200);
        while(std::chrono::steady_clock::now()<deadline){
            XEvent e{};
            if(XCheckTypedWindowEvent(s.x,s.window,SelectionNotify,&e)){
                if(e.xselection.property!=None){
                    Atom type;int format;unsigned long count,remaining;unsigned char* bytes=nullptr;
                    if(XGetWindowProperty(s.x,s.window,s.property,0,16384,True,AnyPropertyType,
                        &type,&format,&count,&remaining,&bytes)==Success&&bytes&&format==8)
                        s.pasted.assign(reinterpret_cast<char*>(bytes),count);
                    if(bytes)XFree(bytes);
                }
                break;
            }
            usleep(1000);
        }
        return s.pasted.c_str();
    };
}
X11ImGui::~X11ImGui(){
    auto& p=*impl;ImGui::GetPlatformIO().ClearPlatformHandlers();
    ImGui::GetIO().BackendPlatformName=nullptr;
    if(p.ic)XDestroyIC(p.ic);
    if(p.im)XCloseIM(p.im);
    for(auto cursor:p.cursors)if(cursor)XFreeCursor(p.x,cursor);
}
bool X11ImGui::poll(){
    auto& p=*impl;auto& io=ImGui::GetIO();
    while(XPending(p.x)){
        XEvent e{};XNextEvent(p.x,&e);
        if(e.type==ClientMessage&&e.xclient.message_type==p.protocols&&Atom(e.xclient.data.l[0])==p.deleteWindow)return false;
        if(e.type==SelectionRequest){p.selection(e.xselectionrequest);continue;}
        if(XFilterEvent(&e,p.window))continue;
        if(e.type==FocusIn){io.AddFocusEvent(true);if(p.ic)XSetICFocus(p.ic);}
        else if(e.type==FocusOut){io.AddFocusEvent(false);if(p.ic)XUnsetICFocus(p.ic);}
        else if(e.type==MotionNotify)io.AddMousePosEvent(float(e.xmotion.x),float(e.xmotion.y));
        else if(e.type==LeaveNotify)io.AddMousePosEvent(-FLT_MAX,-FLT_MAX);
        else if(e.type==ButtonPress||e.type==ButtonRelease){
            bool down=e.type==ButtonPress;auto button=e.xbutton.button;
            io.AddMousePosEvent(float(e.xbutton.x),float(e.xbutton.y));
            if(button<=3)io.AddMouseButtonEvent(button==1?0:button==3?1:2,down);
            else if(down&&button<=7)io.AddMouseWheelEvent(button==6?1.f:button==7?-1.f:0.f,button==4?1.f:button==5?-1.f:0.f);
        }else if(e.type==KeyPress||e.type==KeyRelease){
            bool down=e.type==KeyPress;
            KeySym symbol=XkbKeycodeToKeysym(p.x,KeyCode(e.xkey.keycode),0,0);
            auto k=key(symbol);if(k!=ImGuiKey_None)io.AddKeyEvent(k,down);
            auto modifier=[&](ImGuiKey mod,unsigned mask,KeySym a,KeySym b){
                io.AddKeyEvent(mod,symbol==a||symbol==b?down:bool(e.xkey.state&mask));
            };
            modifier(ImGuiMod_Ctrl,ControlMask,XK_Control_L,XK_Control_R);
            modifier(ImGuiMod_Shift,ShiftMask,XK_Shift_L,XK_Shift_R);
            modifier(ImGuiMod_Alt,Mod1Mask,XK_Alt_L,XK_Alt_R);
            modifier(ImGuiMod_Super,Mod4Mask,XK_Super_L,XK_Super_R);
            if(down&&!(e.xkey.state&(ControlMask|Mod1Mask))){
                char text[128]{};int count=0;
                if(p.ic){Status status;count=Xutf8LookupString(p.ic,&e.xkey,text,127,nullptr,&status);if(status==XBufferOverflow)count=0;}
                else count=XLookupString(&e.xkey,text,127,nullptr,nullptr);
                if(count>0){text[count]=0;io.AddInputCharactersUTF8(text);}
            }
        }
    }
    return true;
}
void X11ImGui::frame(int width,int height){
    auto& p=*impl;auto& io=ImGui::GetIO();
    double now=std::chrono::duration<double>(std::chrono::steady_clock::now().time_since_epoch()).count();
    io.DeltaTime=p.last?float(now-p.last):1.f/60;p.last=now;
    io.DisplaySize={float(width),float(height)};
    int cursor=ImGui::GetMouseCursor();
    if(cursor!=p.lastCursor){
        XDefineCursor(p.x,p.window,p.cursors[cursor>=0&&cursor<ImGuiMouseCursor_COUNT?cursor:0]);p.lastCursor=cursor;
    }
}
void X11ImGui::wait(double seconds){
    if(seconds<=0)return;
    auto deadline=std::chrono::steady_clock::now()+std::chrono::duration<double>(seconds);
    pollfd connection{ConnectionNumber(impl->x),POLLIN,0};
    while(!XPending(impl->x)){
        double remaining=std::chrono::duration<double>(deadline-std::chrono::steady_clock::now()).count();
        if(remaining<=0)return;
        int result=::poll(&connection,1,std::clamp(int(std::ceil(remaining*1000)),1,250));
        if(result<0&&errno!=EINTR)return;
        // GLX replies can make the socket readable without delivering an input
        // event. Let Xlib consume those, then keep waiting for the same deadline.
    }
}
void X11ImGui::click(float x,float y){
    auto& p=*impl;
    for(int type:{ButtonPress,ButtonRelease}){
        XEvent e{};e.xbutton.type=type;e.xbutton.display=p.x;e.xbutton.window=p.window;
        e.xbutton.root=DefaultRootWindow(p.x);e.xbutton.x=int(x);e.xbutton.y=int(y);
        e.xbutton.button=Button1;e.xbutton.same_screen=True;
        XSendEvent(p.x,p.window,False,type==ButtonPress?ButtonPressMask:ButtonReleaseMask,&e);
    }
    XFlush(p.x);
}
void X11ImGui::resize(int w,int h){XResizeWindow(impl->x,impl->window,unsigned(w),unsigned(h));XFlush(impl->x);}
void X11ImGui::close(){
    auto& p=*impl;XEvent e{};e.xclient.type=ClientMessage;e.xclient.window=p.window;
    e.xclient.message_type=p.protocols;e.xclient.format=32;e.xclient.data.l[0]=long(p.deleteWindow);
    XSendEvent(p.x,p.window,False,NoEventMask,&e);XFlush(p.x);
}
