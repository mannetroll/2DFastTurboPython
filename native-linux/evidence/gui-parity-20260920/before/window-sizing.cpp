#include <X11/Xlib.h>
#include <X11/Xutil.h>
#include <X11/Xatom.h>
#include <unistd.h>
#include <sys/wait.h>
#include <cstdlib>
#include <iostream>
Window find(Display* d,Window root,pid_t pid){
    Atom actual;int format;unsigned long count,left;unsigned char* bytes=nullptr;
    XGetWindowProperty(d,root,XInternAtom(d,"_NET_WM_PID",False),0,1,False,XA_CARDINAL,&actual,&format,&count,&left,&bytes);
    bool match=bytes&&count&&*reinterpret_cast<unsigned long*>(bytes)==static_cast<unsigned long>(pid);
    if(bytes)XFree(bytes);if(match)return root;
    Window returned,parent,*children=nullptr;unsigned n=0;
    if(!XQueryTree(d,root,&returned,&parent,&children,&n))return 0;
    Window result=0;for(unsigned i=0;i<n&&!result;++i)result=find(d,children[i],pid);
    if(children)XFree(children);return result;
}
int main(int argc,char** argv){
    if(argc<2)return 2;
    pid_t pid=fork();if(pid==0){execv(argv[1],argv+1);_exit(127);}
    Display* d=XOpenDisplay(nullptr);if(!d)return 3;
    for(int i=0;i<100;++i){
        if(Window w=find(d,DefaultRootWindow(d),pid)){
            XSizeHints hints{};hints.flags=PMinSize;hints.min_width=1920;hints.min_height=1080;
            XSetWMNormalHints(d,w,&hints);XResizeWindow(d,w,1920,1080);XFlush(d);break;
        }
        usleep(50000);
    }
    int status=0;waitpid(pid,&status,0);XCloseDisplay(d);return WIFEXITED(status)?WEXITSTATUS(status):1;
}
