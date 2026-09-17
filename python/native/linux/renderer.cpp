// Linux presentation only. The shared CUDA solver publishes immutable GPU frames.
#define GL_GLEXT_PROTOTYPES
#include <GL/gl.h>
#include <GL/glext.h>
#include <GL/glx.h>
#include <X11/Xlib.h>
#undef Complex // X11 polygon shape macro conflicts with the solver type.
#include <cuda_gl_interop.h>
#include <png.h>
#include "cuda_support.cuh"
#include "render_api.hpp"
#include "render_shaders.hpp"
#include <chrono>
#include <cstring>
#include <sstream>

namespace turbo {
namespace {
double seconds(){return std::chrono::duration<double>(std::chrono::steady_clock::now().time_since_epoch()).count();}
void glCheck(const char* action){auto code=glGetError();if(code)throw std::runtime_error(std::string(action)+": OpenGL error "+std::to_string(code));}
GLuint shader(GLenum type,const char* body){
    GLuint s=glCreateShader(type);const char* sources[]={"#version 330 compatibility\n",shaderCommon,body};
    glShaderSource(s,3,sources,nullptr);glCompileShader(s);GLint ok=0;glGetShaderiv(s,GL_COMPILE_STATUS,&ok);
    if(!ok){char log[4096]{};glGetShaderInfoLog(s,sizeof(log),nullptr,log);glDeleteShader(s);throw std::runtime_error(std::string("GLSL compile: ")+log);}return s;
}
GLuint program(const char* vertex,const char* fragment){
    GLuint v=shader(GL_VERTEX_SHADER,vertex),f=0,p=0;
    try{f=shader(GL_FRAGMENT_SHADER,fragment);p=glCreateProgram();glAttachShader(p,v);glAttachShader(p,f);glLinkProgram(p);
        GLint ok=0;glGetProgramiv(p,GL_LINK_STATUS,&ok);if(!ok){char log[4096]{};glGetProgramInfoLog(p,sizeof(log),nullptr,log);throw std::runtime_error(std::string("GLSL link: ")+log);}
    }catch(...){glDeleteShader(v);if(f)glDeleteShader(f);if(p)glDeleteProgram(p);throw;}
    glDeleteShader(v);glDeleteShader(f);return p;
}
class Renderer final:public LinuxRenderer {
    ::Display* x=nullptr;
    Window parent=0,window=0;
    Colormap colormap=0;
    GLXContext context=nullptr;
    GLuint fieldProgram=0,flowProgram=0,buffer=0,texture=0,vao=0,font=0;
    cudaGraphicsResource* interop=nullptr;
    cudaStream_t stream=nullptr;
    std::shared_ptr<Frame> uploaded;
    int n=0,width=64,height=64;
    double scale=1;
    void release() noexcept {
        if(context&&x&&window)glXMakeCurrent(x,window,context);
        if(stream)cudaStreamSynchronize(stream);
        if(interop)cudaGraphicsUnregisterResource(interop);
        uploaded.reset();
        if(context){
            glFinish();if(buffer)glDeleteBuffers(1,&buffer);if(texture)glDeleteTextures(1,&texture);
            if(vao)glDeleteVertexArrays(1,&vao);
            if(fieldProgram)glDeleteProgram(fieldProgram);
            if(flowProgram)glDeleteProgram(flowProgram);
            if(font)glDeleteLists(font,128);
        }
        if(stream)cudaStreamDestroy(stream);
        if(context){glXMakeCurrent(x,None,nullptr);glXDestroyContext(x,context);}
        if(window)XDestroyWindow(x,window);
        if(colormap)XFreeColormap(x,colormap);
        if(x)XCloseDisplay(x);
    }
    RenderTiming upload(const std::shared_ptr<Frame>& f){
        RenderTiming timing;if(uploaded==f)return timing;
        double started=seconds();size_t bytes=size_t(3)*f->n*f->n*sizeof(float);
        if(n!=f->n){
            if(interop){CUDA_CHECK(cudaGraphicsUnregisterResource(interop));interop=nullptr;}
            glBindBuffer(GL_TEXTURE_BUFFER,buffer);glBufferData(GL_TEXTURE_BUFFER,GLsizeiptr(bytes),nullptr,GL_DYNAMIC_DRAW);
            glBindTexture(GL_TEXTURE_BUFFER,texture);glTexBuffer(GL_TEXTURE_BUFFER,GL_R32F,buffer);glCheck("Allocate shared field");
            CUDA_CHECK(cudaGraphicsGLRegisterBuffer(&interop,buffer,cudaGraphicsRegisterFlagsWriteDiscard));n=f->n;
        }
        CUDA_CHECK(cudaGraphicsMapResources(1,&interop,stream));bool mapped=true;
        try{
            void* pointer=nullptr;size_t available=0;CUDA_CHECK(cudaGraphicsResourceGetMappedPointer(&pointer,&available,interop));
            if(available<bytes)throw std::runtime_error("Shared OpenGL buffer is too small.");
            CUDA_CHECK(cudaMemcpyAsync(pointer,f->data(),bytes,cudaMemcpyDeviceToDevice,stream));
            CUDA_CHECK(cudaGraphicsUnmapResources(1,&interop,stream));mapped=false;
            // The frame lease remains held until the copy finishes. Only this
            // interop stream is waited on; the solver owns a different stream.
            CUDA_CHECK(cudaStreamSynchronize(stream));uploaded=f;
        }catch(...){if(mapped)cudaGraphicsUnmapResources(1,&interop,stream);cudaStreamSynchronize(stream);throw;}
        timing.uploadSeconds=seconds()-started;timing.uploads=1;timing.deviceCopyBytes=bytes;return timing;
    }
    void uniforms(GLuint p,const Frame& f,const Display& d){
        glUseProgram(p);
        auto integer=[&](const char* key,int v){glUniform1i(glGetUniformLocation(p,key),v);};
        auto real=[&](const char* key,float v){glUniform1f(glGetUniformLocation(p,key),v);};
        integer("field",0);integer("gridSize",f.n);integer("palette",d.palette);integer("display",d.field);integer("vorticityColorRange",d.colorRange);
        real("exposure",d.exposure);real("vorticityMean",f.mean);
        real("vorticityScale",d.field==0&&d.colorRange==0?std::max(d.sigma*f.sigma,1e-12f):std::max(f.maxOmega,.0001f));
        real("speedScale",std::max(f.maxSpeed,.0001f));
    }
    void draw(const Frame& f,const Display& d,int side,float radius=0){
        glViewport(0,0,side,side);glDisable(GL_FRAMEBUFFER_SRGB);glDisable(GL_DEPTH_TEST);glDisable(GL_CULL_FACE);glDisable(GL_BLEND);
        glActiveTexture(GL_TEXTURE0);glBindTexture(GL_TEXTURE_BUFFER,texture);glBindVertexArray(vao);
        uniforms(fieldProgram,f,d);
        glUniform1f(glGetUniformLocation(fieldProgram,"canvasPixels"),float(side));
        glUniform1f(glGetUniformLocation(fieldProgram,"rounded"),radius);
        glDrawArrays(GL_TRIANGLES,0,3);
        if(d.flow&&f.hasVelocity){glEnable(GL_BLEND);glBlendFunc(GL_SRC_ALPHA,GL_ONE_MINUS_SRC_ALPHA);uniforms(flowProgram,f,d);glDrawArrays(GL_LINES,0,32*32*18*2);}
        glUseProgram(0);glBindTexture(GL_TEXTURE_BUFFER,0);glBindVertexArray(0);glCheck("Draw field");
    }
    void rectangle(float left,float top,float right,float bottom){
        float radius=std::min(18.f,(bottom-top)/2);
        glColor4f(0,0,0,.35f);glBegin(GL_TRIANGLE_FAN);glVertex2f((left+right)/2,(top+bottom)/2);
        for(int corner=0;corner<4;++corner){
            float cx=(corner==0||corner==3)?right-radius:left+radius;
            float cy=corner<2?bottom-radius:top+radius;
            for(int j=0;j<=8;++j){double angle=(corner+j/8.)*3.14159265358979323846/2;glVertex2f(cx+radius*float(std::cos(angle)),cy+radius*float(std::sin(angle)));}
        }
        glVertex2f(right,bottom-radius);glEnd();
    }
    void text(float left,float top,const std::string& value){
        if(!font)return;
        glColor4f(.9f,.92f,.95f,1);glRasterPos2f(left,top);glListBase(font);glCallLists(GLsizei(value.size()),GL_UNSIGNED_BYTE,value.data());
    }
    void overlay(uint64_t seed,double time,bool running){
        float w=float(width/scale),h=float(height/scale);
        glViewport(0,0,width,height);glMatrixMode(GL_PROJECTION);glLoadIdentity();glOrtho(0,w,h,0,-1,1);glMatrixMode(GL_MODELVIEW);glLoadIdentity();
        glEnable(GL_BLEND);glBlendFunc(GL_SRC_ALPHA,GL_ONE_MINUS_SRC_ALPHA);
        std::string caption="UNFORCED - SEED "+std::to_string(seed);
        rectangle(14,14,float(34+caption.size()*6),40);text(24,31,caption);
        rectangle(14,h-49,128,h-14);glColor4f(.9f,.92f,.95f,1);
        if(running){glBegin(GL_QUADS);for(float left:{28.f,35.f}){glVertex2f(left,h-39);glVertex2f(left+3,h-39);glVertex2f(left+3,h-24);glVertex2f(left,h-24);}glEnd();}
        else{glBegin(GL_TRIANGLES);glVertex2f(29,h-40);glVertex2f(29,h-23);glVertex2f(42,h-31.5f);glEnd();}
        glBegin(GL_TRIANGLES);glVertex2f(65,h-40);glVertex2f(65,h-23);glVertex2f(75,h-31.5f);glEnd();
        glBegin(GL_LINES);glVertex2f(78,h-40);glVertex2f(78,h-23);glEnd();
        glBegin(GL_LINE_STRIP);
        for(int i=0;i<=24;++i){double a=(45+i*270./24)*3.14159265358979323846/180;glVertex2f(109+7*float(std::cos(a)),h-32+7*float(std::sin(a)));}
        glEnd();glBegin(GL_TRIANGLES);glVertex2f(110,h-42);glVertex2f(116,h-36);glVertex2f(110,h-36);glEnd();
        std::ostringstream clock;clock.precision(3);clock<<"t "<<std::fixed<<time;
        rectangle(w-114,h-46,w-14,h-17);text(w-104,h-27,clock.str());glDisable(GL_BLEND);
    }
public:
    Renderer(uintptr_t host,int device):parent(Window(host)){
        try{
            // Also cover explicit Engine PNG exports without Qt initialization.
            if(!XInitThreads())throw std::runtime_error("X11 thread initialization failed.");
            CUDA_CHECK(cudaSetDevice(device));
            x=XOpenDisplay(nullptr);if(!x)throw std::runtime_error("OpenGL rendering requires a local X11/Xwayland display (DISPLAY).");
            int attributes[]={GLX_X_RENDERABLE,True,GLX_DRAWABLE_TYPE,GLX_WINDOW_BIT,GLX_RENDER_TYPE,GLX_RGBA_BIT,
                GLX_DOUBLEBUFFER,True,GLX_RED_SIZE,8,GLX_GREEN_SIZE,8,GLX_BLUE_SIZE,8,None};
            int count=0;auto configs=glXChooseFBConfig(x,DefaultScreen(x),attributes,&count);
            if(!configs||!count){if(configs)XFree(configs);throw std::runtime_error("No OpenGL framebuffer configuration.");}
            auto config=configs[0];auto visual=glXGetVisualFromFBConfig(x,config);XFree(configs);
            if(!visual)throw std::runtime_error("No OpenGL visual.");
            Window hostWindow=parent?parent:RootWindow(x,visual->screen);
            colormap=XCreateColormap(x,hostWindow,visual->visual,AllocNone);
            XSetWindowAttributes wa{};wa.colormap=colormap;wa.event_mask=ButtonReleaseMask|KeyPressMask|KeyReleaseMask;
            window=XCreateWindow(x,hostWindow,0,0,width,height,0,visual->depth,InputOutput,visual->visual,CWColormap|CWEventMask,&wa);XFree(visual);
            context=glXCreateNewContext(x,config,GLX_RGBA_TYPE,nullptr,True);
            if(!context||!glXMakeCurrent(x,window,context))throw std::runtime_error("Cannot create the native OpenGL context.");
            GLint major=0,minor=0;glGetIntegerv(GL_MAJOR_VERSION,&major);glGetIntegerv(GL_MINOR_VERSION,&minor);
            if(major<3||(major==3&&minor<3))throw std::runtime_error("Native rendering needs OpenGL 3.3 or later.");
            unsigned devices=0;int ids[16]{};auto code=cudaGLGetDevices(&devices,ids,16,cudaGLDeviceListAll);
            if(code!=cudaSuccess)throw std::runtime_error(std::string("CUDA/OpenGL sharing is unavailable on this display: ")+cudaGetErrorString(code)+". WSLg sharing is not supported by the tested driver stack; headless CUDA remains available.");
            if(std::find(ids,ids+devices,device)==ids+devices)throw std::runtime_error("OpenGL and CUDA selected different GPUs.");
            CUDA_CHECK(cudaStreamCreateWithFlags(&stream,cudaStreamNonBlocking));
            glGenBuffers(1,&buffer);glGenTextures(1,&texture);glGenVertexArrays(1,&vao);
            fieldProgram=program(fieldVertex,fieldPixel);flowProgram=program(flowVertex,flowPixel);
            if(auto xf=XLoadQueryFont(x,"fixed")){font=glGenLists(128);glXUseXFont(xf->fid,0,128,int(font));XFreeFont(x,xf);}
            // A native 60 Hz deadline controls presentation; no vsync wait can
            // constrain the independent simulation worker.
            using SwapInterval=void(*)(::Display*,GLXDrawable,int);
            if(auto interval=reinterpret_cast<SwapInterval>(glXGetProcAddressARB(reinterpret_cast<const GLubyte*>("glXSwapIntervalEXT"))))interval(x,window,0);
            if(parent)XMapWindow(x,window);
            XFlush(x);glCheck("Initialize renderer");
        }catch(...){release();throw;}
    }
    ~Renderer() override{release();}
    CanvasInfo geometry() override{
        if(!parent)return {width,height,false};
        XWindowAttributes a{};if(!XGetWindowAttributes(x,parent,&a))throw std::runtime_error("The canvas window was destroyed.");
        if(a.width!=width||a.height!=height){width=std::max(a.width,1);height=std::max(a.height,1);XResizeWindow(x,window,unsigned(width),unsigned(height));}
        return {width,height,a.map_state==IsViewable};
    }
    int event() override{
        while(XPending(x)){
            XEvent e{};XNextEvent(x,&e);
            if((e.type==KeyPress||e.type==KeyRelease)&&parent){e.xkey.window=parent;XSendEvent(x,parent,True,e.type==KeyPress?KeyPressMask:KeyReleaseMask,&e);}
            if(e.type==ButtonRelease&&e.xbutton.button==Button1){
                double px=e.xbutton.x/scale,py=e.xbutton.y/scale;
                if(px>=14&&px<128&&py>=height/scale-49&&py<=height/scale-14)return 1+int((px-14)/38);
            }
        }return 0;
    }
    RenderTiming present(const std::shared_ptr<Frame>& f,const Display& d,uint64_t seed,double time,bool running,double dpi) override{
        scale=dpi;RenderTiming t;double started=seconds();
        glBindFramebuffer(GL_FRAMEBUFFER,0);glClearColor(.035f,.047f,.075f,1);glClear(GL_COLOR_BUFFER_BIT);
        if(f){t=upload(f);draw(*f,d,std::min(width,height),float(15*scale));}
        overlay(seed,time,running);t.drawSeconds=seconds()-started-t.uploadSeconds;
        started=seconds();glXSwapBuffers(x,window);t.swapSeconds=seconds()-started;return t;
    }
    void exportPNG(const std::shared_ptr<Frame>& f,const Display& d,const std::filesystem::path& path,int side) override{
        GLuint target=0,pixels=0;
        try{
            upload(f);glGenTextures(1,&pixels);glBindTexture(GL_TEXTURE_2D,pixels);glTexImage2D(GL_TEXTURE_2D,0,GL_RGBA8,side,side,0,GL_RGBA,GL_UNSIGNED_BYTE,nullptr);
            glGenFramebuffers(1,&target);glBindFramebuffer(GL_FRAMEBUFFER,target);glFramebufferTexture2D(GL_FRAMEBUFFER,GL_COLOR_ATTACHMENT0,GL_TEXTURE_2D,pixels,0);
            if(glCheckFramebufferStatus(GL_FRAMEBUFFER)!=GL_FRAMEBUFFER_COMPLETE)throw std::runtime_error("PNG framebuffer is incomplete.");
            draw(*f,d,side);
            // Explicit PNG request only. Normal presentation performs no readback.
            std::vector<unsigned char> rgba(size_t(side)*side*4),top(rgba.size());
            glReadPixels(0,0,side,side,GL_RGBA,GL_UNSIGNED_BYTE,rgba.data());glCheck("Read PNG pixels");
            for(int y=0;y<side;++y)std::copy_n(rgba.data()+size_t(side-1-y)*side*4,size_t(side)*4,top.data()+size_t(y)*side*4);
            png_image image{};image.version=PNG_IMAGE_VERSION;image.width=unsigned(side);image.height=unsigned(side);image.format=PNG_FORMAT_RGBA;
            if(!png_image_write_to_file(&image,path.c_str(),0,top.data(),0,nullptr))throw std::runtime_error(std::string("Write PNG: ")+image.message);
        }catch(...){glBindFramebuffer(GL_FRAMEBUFFER,0);if(target)glDeleteFramebuffers(1,&target);if(pixels)glDeleteTextures(1,&pixels);throw;}
        glBindFramebuffer(GL_FRAMEBUFFER,0);glDeleteFramebuffers(1,&target);glDeleteTextures(1,&pixels);
    }
};
}
}
extern "C" turbo::LinuxRenderer* ft_gl_create(uintptr_t parent,int device){return new turbo::Renderer(parent,device);}
