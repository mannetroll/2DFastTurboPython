#pragma once
#include "imgui.h"
#include <algorithm>
#include <filesystem>
#include <cmath>

// Presentation constants follow reference-windows/src/app.cpp and its UI legend.
// Field colors are still produced exclusively by the existing GPU shaders.
namespace desktop {
inline constexpr ImVec4 background{.031f,.043f,.059f,1}, panel{.061f,.076f,.106f,1};
inline constexpr ImVec4 foreground{.90f,.90f,.90f,1}, secondary{.48f,.55f,.65f,1};
inline constexpr ImVec4 accent{.44f,.91f,.82f,1}, border{1,1,1,.075f};
inline ImFont* mono=nullptr;
inline const char* fontName="Dear ImGui built-in";
inline ImU32 color(ImVec4 c){return ImGui::ColorConvertFloat4ToU32(c);}
inline void fonts(){
    auto& atlas=*ImGui::GetIO().Fonts;
    for(const char* path:{"/usr/share/fonts/truetype/ubuntu/Ubuntu[wdth,wght].ttf",
                         "/usr/share/fonts/truetype/ubuntu/Ubuntu-R.ttf",
                         "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
                         "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"}){
        if(std::filesystem::is_regular_file(path)&&atlas.AddFontFromFileTTF(path,14)){
            fontName=path;break;
        }
    }
    if(atlas.Fonts.empty())atlas.AddFontDefault();
    for(const char* path:{"/usr/share/fonts/truetype/ubuntu/UbuntuMono[wght].ttf",
                         "/usr/share/fonts/truetype/ubuntu/UbuntuMono-R.ttf",
                         "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"}){
        if(std::filesystem::is_regular_file(path)&&(mono=atlas.AddFontFromFileTTF(path,14)))break;
    }
    if(!mono)mono=atlas.Fonts[0];
}
inline void style(float scale){
    auto& s=ImGui::GetStyle();s=ImGuiStyle{};
    s.WindowPadding={14,10};s.FramePadding={8,4};s.ItemSpacing={8,6};s.ItemInnerSpacing={8,6};
    s.WindowRounding=10;s.ChildRounding=10;s.FrameRounding=5;s.PopupRounding=8;
    s.WindowBorderSize=1;s.ChildBorderSize=1;s.FrameBorderSize=0;
    s.ScrollbarSize=9;s.ScrollbarRounding=6;s.GrabMinSize=8;s.GrabRounding=4;
    s.SeparatorTextPadding={0,8};s.SeparatorTextBorderSize=1;
    auto* c=s.Colors;
    c[ImGuiCol_Text]=foreground;c[ImGuiCol_TextDisabled]=secondary;
    c[ImGuiCol_WindowBg]=background;c[ImGuiCol_ChildBg]=panel;c[ImGuiCol_PopupBg]=panel;
    c[ImGuiCol_TitleBg]=panel;c[ImGuiCol_TitleBgActive]={.08f,.12f,.15f,1};c[ImGuiCol_TitleBgCollapsed]=panel;
    c[ImGuiCol_Border]=border;c[ImGuiCol_BorderShadow]={0,0,0,0};
    c[ImGuiCol_FrameBg]={.105f,.13f,.17f,1};c[ImGuiCol_FrameBgHovered]={.14f,.18f,.22f,1};
    c[ImGuiCol_FrameBgActive]={.16f,.22f,.25f,1};
    c[ImGuiCol_Button]={.085f,.11f,.145f,1};c[ImGuiCol_ButtonHovered]={.14f,.20f,.22f,1};
    c[ImGuiCol_ButtonActive]={.19f,.31f,.31f,1};
    c[ImGuiCol_Header]={.14f,.24f,.25f,1};c[ImGuiCol_HeaderHovered]={.17f,.28f,.29f,1};
    c[ImGuiCol_HeaderActive]={.21f,.34f,.34f,1};
    c[ImGuiCol_CheckMark]=accent;c[ImGuiCol_SliderGrab]={.28f,.64f,.58f,1};c[ImGuiCol_SliderGrabActive]=accent;
    c[ImGuiCol_Separator]=border;c[ImGuiCol_SeparatorHovered]=accent;c[ImGuiCol_SeparatorActive]=accent;
    c[ImGuiCol_ScrollbarBg]={0,0,0,0};c[ImGuiCol_ScrollbarGrab]={.23f,.28f,.34f,1};
    c[ImGuiCol_ScrollbarGrabHovered]={.32f,.40f,.46f,1};c[ImGuiCol_ScrollbarGrabActive]=accent;
    c[ImGuiCol_TextSelectedBg]={.26f,.60f,.55f,.35f};c[ImGuiCol_NavCursor]=accent;
    c[ImGuiCol_ModalWindowDimBg]={0,0,0,.55f};
    s.ScaleAllSizes(scale);s.FontScaleDpi=scale;
}
inline void mark(ImVec2 center,float radius){
    // Same outline as reference-windows/assets/header-hurricane.svg (source credit there).
    // Quadratic control/end points in that asset's coordinates; no SVG runtime required.
    static constexpr float curves[][4]={
        {4.9629f, 15.6621f, 5.5000f, 16.1777f},
        {6.0371f, 16.6934f, 6.8535f, 16.6934f},
        {10.8711f, 16.6934f, 14.5234f, 17.6924f},
        {18.1758f, 18.6914f, 21.2480f, 20.5713f},
        {24.3203f, 22.4512f, 26.6836f, 25.0938f},
        {29.0469f, 27.7363f, 30.4863f, 31.0234f},
        {28.8965f, 29.5625f, 26.7910f, 28.7246f},
        {24.6855f, 27.8867f, 22.3223f, 27.8867f},
        {19.4863f, 27.8867f, 16.9834f, 28.9717f},
        {14.4805f, 30.0566f, 12.5791f, 31.9688f},
        {10.6777f, 33.8809f, 9.5928f, 36.3838f},
        {8.5078f, 38.8867f, 8.5078f, 41.7227f},
        {8.5078f, 46.8574f, 9.9688f, 51.2617f},
        {11.4297f, 55.6660f, 14.0938f, 59.1895f},
        {16.7578f, 62.7129f, 20.4102f, 65.2051f},
        {24.0625f, 67.6973f, 28.4775f, 69.0186f},
        {32.8926f, 70.3398f, 37.7910f, 70.3398f},
        {38.6289f, 70.3398f, 39.1553f, 69.8242f},
        {39.6816f, 69.3086f, 39.6816f, 68.5566f},
        {39.6816f, 67.8047f, 39.1553f, 67.2783f},
        {38.6289f, 66.7520f, 37.7910f, 66.7520f},
        {33.7520f, 66.7520f, 30.1104f, 65.7529f},
        {26.4688f, 64.7539f, 23.3857f, 62.8848f},
        {20.3027f, 61.0156f, 17.9502f, 58.3730f},
        {15.5977f, 55.7305f, 14.1582f, 52.4219f},
        {15.7480f, 53.8828f, 17.8535f, 54.7314f},
        {19.9590f, 55.5801f, 22.3223f, 55.5801f},
        {25.1582f, 55.5801f, 27.6611f, 54.4951f},
        {30.1641f, 53.4102f, 32.0762f, 51.4980f},
        {33.9883f, 49.5859f, 35.0732f, 47.0830f},
        {36.1582f, 44.5801f, 36.1582f, 41.7227f},
        {36.1582f, 36.6094f, 34.6973f, 32.2051f},
        {33.2363f, 27.8008f, 30.5615f, 24.2773f},
        {27.8867f, 20.7539f, 24.2344f, 18.2617f},
        {20.5820f, 15.7695f, 16.1777f, 14.4375f},
        {11.7734f, 13.1055f, 6.8535f, 13.1055f},
        {6.0371f, 13.1055f, 5.5000f, 13.6318f},
        {4.9629f, 14.1582f, 4.9629f, 14.9102f},
    };
    auto* d=ImGui::GetWindowDrawList();
    d->AddCircle(center,radius,color({1,1,1,.23f}),40,std::max(1.f,radius/20));
    auto p=[&](float x,float y){return ImVec2(center.x+(x-22.3223f)*radius*.02049f,
                                            center.y-(y-41.7227f)*radius*.02049f);};
    d->PathLineTo(p(4.9629f,14.9102f));
    for(const auto& q:curves)d->PathBezierQuadraticCurveTo(p(q[0],q[1]),p(q[2],q[3]));
    d->PathFillConcave(color(foreground));
    d->AddCircleFilled(center,radius*.0785f,color(background),16);
}
inline void gradient(ImVec2 pos,ImVec2 size,int palette){
    static constexpr ImVec4 stops[4][3]={
        {{.22f,.25f,.82f,1},{.06f,.09f,.18f,1},{.39f,.94f,.73f,1}},
        {{.42f,.14f,.57f,1},{.12f,.04f,.1f,1},{1,.64f,.27f,1}},
        {{.12f,.29f,.58f,1},{.03f,.12f,.19f,1},{.64f,.94f,1,1}},
        {{.06f,.03f,.16f,1},{.68f,.16f,.28f,1},{.99f,.93f,.56f,1}}};
    auto* d=ImGui::GetWindowDrawList();float middle=pos.x+size.x*.5f;
    d->AddRectFilledMultiColor(pos,{middle,pos.y+size.y},color(stops[palette][0]),color(stops[palette][1]),color(stops[palette][1]),color(stops[palette][0]));
    d->AddRectFilledMultiColor({middle,pos.y},{pos.x+size.x,pos.y+size.y},color(stops[palette][1]),color(stops[palette][2]),color(stops[palette][2]),color(stops[palette][1]));
}
inline void hint(const char* text){
    if(ImGui::IsItemHovered(ImGuiHoveredFlags_DelayNormal|ImGuiHoveredFlags_AllowWhenDisabled)){
        ImGui::BeginTooltip();ImGui::PushTextWrapPos(ImGui::GetFontSize()*28);
        ImGui::TextUnformatted(text);ImGui::PopTextWrapPos();ImGui::EndTooltip();
    }
}
}
