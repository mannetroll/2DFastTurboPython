#pragma once
#include "theme.hpp"
#include "imgui_internal.h"

// The existing theme/icon remains intact. These measurements follow GUI.png:
// 196 px inspector, 18 px gap, 77 px header, 34 px legend, 54 px metrics.
namespace presentation {
inline constexpr ImVec4 accent{.09f,.88f,.80f,1};
inline ImFont* titleFont=nullptr;
inline void fonts(){
    desktop::fonts();
    const char* path="/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf";
    if(std::filesystem::is_regular_file(path))titleFont=ImGui::GetIO().Fonts->AddFontFromFileTTF(path,16);
}
inline void style(float scale){
    desktop::style(scale);
    auto& s=ImGui::GetStyle();
    s.FontSizeBase=11;s.WindowPadding={14*scale,14*scale};
    s.ItemSpacing={6*scale,6*scale};s.FramePadding={5*scale,5*scale};
    s.FrameBorderSize=scale;s.ChildRounding=12*scale;s.WindowRounding=0;
    s.Colors[ImGuiCol_WindowBg]={.031f,.043f,.063f,1};
    s.Colors[ImGuiCol_ChildBg]={.055f,.071f,.098f,1};
    s.Colors[ImGuiCol_MenuBarBg]=s.Colors[ImGuiCol_ChildBg];
    s.Colors[ImGuiCol_Border]={.15f,.18f,.23f,1};
    s.Colors[ImGuiCol_FrameBg]={.10f,.125f,.165f,1};
    s.Colors[ImGuiCol_CheckMark]=accent;s.Colors[ImGuiCol_NavCursor]=accent;
    s.Colors[ImGuiCol_SliderGrab]=accent;s.Colors[ImGuiCol_SliderGrabActive]=accent;
}
inline void text(ImVec2 p,const char* value,float size=11,ImVec4 color=desktop::foreground,bool mono=false){
    ImGui::PushFont(mono?desktop::mono:nullptr,size);
    ImGui::GetWindowDrawList()->AddText(p,ImGui::GetColorU32(color),value);
    ImGui::PopFont();
}
inline void title(ImVec2 p,float scale){
    ImGui::PushFont(titleFont,16);
    for(const char* c="2D FAST TURBO";*c;++c){
        ImGui::GetWindowDrawList()->AddText(p,ImGui::GetColorU32(desktop::foreground),c,c+1);
        p.x+=ImGui::CalcTextSize(c,c+1).x+1.2f*scale;
    }
    ImGui::PopFont();
}
inline void panel(ImVec2 p,ImVec2 size,float scale,float round=12){
    auto* d=ImGui::GetWindowDrawList();ImVec2 end{p.x+size.x,p.y+size.y};
    d->AddRectFilled(p,end,ImGui::GetColorU32(ImGuiCol_ChildBg),round*scale);
    d->AddRect(p,end,ImGui::GetColorU32(ImGuiCol_Border),round*scale);
}
inline bool choiceButton(const char* title,bool selected,float width,float scale,int palette=-1){
    ImGui::PushID(title);ImVec2 p=ImGui::GetCursorScreenPos(),size{width,24*scale};
    ImGui::PushStyleColor(ImGuiCol_Button,selected?ImVec4{.095f,.18f,.20f,1}:ImVec4{.087f,.11f,.15f,1});
    bool pressed=ImGui::Button("##choice",size);ImGui::PopStyleColor();
    auto* d=ImGui::GetWindowDrawList();
    if(palette>=0){
        // Clip the reference's full-width palette preview to rounded corners.
        int begin=d->VtxBuffer.Size;
        d->AddRectFilled({p.x+scale,p.y+scale},{p.x+size.x-scale,p.y+size.y-scale},IM_COL32_WHITE,4*scale);
        static constexpr ImVec4 stops[4][3]={
            {{.22f,.25f,.82f,1},{.06f,.09f,.18f,1},{0,.84f,.65f,1}},
            {{.42f,.14f,.57f,1},{.12f,.04f,.1f,1},{1,.58f,.08f,1}},
            {{.03f,.28f,.58f,1},{0,.13f,.19f,1},{.47f,.88f,.96f,1}},
            {{.06f,.03f,.16f,1},{.68f,.04f,.24f,1},{.99f,.88f,.48f,1}}};
        for(int i=begin;i<d->VtxBuffer.Size;++i){
            auto& v=d->VtxBuffer[i];float t=std::clamp((v.pos.x-p.x)/size.x,0.f,1.f);
            // Add a middle stop through separate left/right shading below.
            auto a=stops[palette][0],b=stops[palette][2];
            v.col=ImGui::GetColorU32(ImVec4{a.x+(b.x-a.x)*t,a.y+(b.y-a.y)*t,a.z+(b.z-a.z)*t,1});
        }
        // Central rectangles provide the dark center of diverging palettes.
        d->PushClipRect({p.x+4*scale,p.y+scale},{p.x+size.x-4*scale,p.y+size.y-scale},true);
        float mid=p.x+size.x*.5f;
        d->AddRectFilledMultiColor({p.x,p.y+scale},{mid,p.y+size.y-scale},ImGui::GetColorU32(stops[palette][0]),ImGui::GetColorU32(stops[palette][1]),ImGui::GetColorU32(stops[palette][1]),ImGui::GetColorU32(stops[palette][0]));
        d->AddRectFilledMultiColor({mid,p.y+scale},{p.x+size.x,p.y+size.y-scale},ImGui::GetColorU32(stops[palette][1]),ImGui::GetColorU32(stops[palette][2]),ImGui::GetColorU32(stops[palette][2]),ImGui::GetColorU32(stops[palette][1]));
        d->PopClipRect();
    }
    d->AddRect(p,{p.x+size.x,p.y+size.y},ImGui::GetColorU32(selected?accent:ImGui::GetStyleColorVec4(ImGuiCol_Border)),5*scale,0,scale);
    text({p.x+8*scale,p.y+6*scale},title,11,palette>=0?desktop::foreground:selected?accent:desktop::foreground);
    ImGui::PopID();return pressed;
}
inline bool toggle(const char* label,bool* value,float scale){
    ImGui::PushID(label);auto p=ImGui::GetCursorScreenPos();
    ImGui::PushStyleVar(ImGuiStyleVar_FrameBorderSize,0);
    bool hit=ImGui::Button("##toggle",{ImGui::GetContentRegionAvail().x,16*scale});
    ImGui::PopStyleVar();
    if(hit)*value=!*value;
    auto* d=ImGui::GetWindowDrawList();
    d->AddRectFilled(p,{p.x+ImGui::GetItemRectSize().x,p.y+16*scale},ImGui::GetColorU32(ImGuiCol_ChildBg));
    d->AddRectFilled({p.x,p.y+2*scale},{p.x+22*scale,p.y+14*scale},ImGui::GetColorU32(*value?accent:ImVec4{.20f,.26f,.33f,1}),6*scale);
    if(*value)d->AddCircleFilled({p.x+16*scale,p.y+8*scale},4*scale,IM_COL32(230,242,238,255));
    text({p.x+27*scale,p.y+2*scale},label,10);
    if(ImGui::IsItemFocused())d->AddRect(p,{p.x+ImGui::GetItemRectSize().x,p.y+16*scale},ImGui::GetColorU32(accent),3*scale);
    ImGui::PopID();return hit;
}
inline bool slider(const char* label,float* value,float low,float high,const char* formatted,float scale,ImGuiSliderFlags flags=0){
    ImGui::PushID(label);auto p=ImGui::GetCursorScreenPos();float width=ImGui::GetContentRegionAvail().x;
    text(p,label);ImGui::PushFont(desktop::mono,11);float tw=ImGui::CalcTextSize(formatted).x;ImGui::PopFont();
    text({p.x+width-tw,p.y},formatted,11,accent,true);
    ImGui::Dummy({width,13*scale});ImGui::SetNextItemWidth(width);
    ImGui::PushStyleVar(ImGuiStyleVar_FramePadding,{0,1*scale});
    ImGui::PushStyleVar(ImGuiStyleVar_FrameBorderSize,0);
    ImGui::PushStyleColor(ImGuiCol_FrameBg,{0,0,0,0});
    ImGui::PushStyleColor(ImGuiCol_SliderGrab,{0,0,0,0});ImGui::PushStyleColor(ImGuiCol_SliderGrabActive,{0,0,0,0});
    bool hit=ImGui::SliderFloat("##value",value,low,high,"",flags|ImGuiSliderFlags_AlwaysClamp);
    if(!ImGui::TempInputIsActive(ImGui::GetItemID())){
        auto a=ImGui::GetItemRectMin(),b=ImGui::GetItemRectMax();float inset=5*scale,y=(a.y+b.y)*.5f;
        float fraction=(flags&ImGuiSliderFlags_Logarithmic)?std::log(*value/low)/std::log(high/low):(*value-low)/(high-low);
        float x=a.x+inset+(b.x-a.x-2*inset)*std::clamp(fraction,0.f,1.f);
        auto* d=ImGui::GetWindowDrawList();d->AddLine({a.x,y},{b.x,y},IM_COL32(51,65,80,255),3*scale);
        d->AddLine({a.x,y},{x,y},ImGui::GetColorU32(accent),3*scale);
        d->AddCircleFilled({x,y},5*scale,ImGui::GetColorU32(ImVec4{.84f,.93f,.91f,1}));
    }
    ImGui::PopStyleColor(3);ImGui::PopStyleVar(2);ImGui::PopID();return hit;
}
// Vector icons are crisp at every DPI; no platform-dependent symbol font.
inline void icon(ImVec2 p,int kind,float scale,bool running=false){
    auto* d=ImGui::GetWindowDrawList();auto c=ImGui::GetColorU32(desktop::foreground);
    auto pt=[&](float x,float y){return ImVec2{p.x+x*scale,p.y+y*scale};};
    if(kind==0&&running){d->AddRectFilled(pt(-4,-5),pt(-1,5),c);d->AddRectFilled(pt(1,-5),pt(4,5),c);}
    else if(kind<=1){if(kind==1)d->AddRectFilled(pt(-6,-5),pt(-4,5),c);d->AddTriangleFilled(pt(-2,-5),pt(-2,5),pt(5,0),c);}
    else if(kind==2){d->PathArcTo(p,6*scale,-1.4f,3.8f,20);d->PathStroke(c,0,scale);d->AddTriangleFilled(pt(-5,-7),pt(-5,-1),pt(-1,-4),c);}
    else if(kind==3){for(float x:{-1.f,1.f})for(float y:{-1.f,1.f}){d->AddLine(pt(x*5,y*2),pt(x*5,y*5),c,scale);d->AddLine(pt(x*2,y*5),pt(x*5,y*5),c,scale);}}
    else {d->AddLine(pt(0,4),pt(0,-5),c,scale);d->AddTriangleFilled(pt(-3,-2),pt(3,-2),pt(0,-6),c);d->AddLine(pt(-4,5),pt(4,5),c,scale);}
}
}
