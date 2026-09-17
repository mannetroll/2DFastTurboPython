#pragma once
#include "render_api.hpp"
#include <memory>
#include <string>
void initializeLinuxUI(const char* resources,const std::string& gpu);
bool linuxRenderingBuilt();
std::unique_ptr<turbo::LinuxRenderer> linuxRenderer(uintptr_t parent=0);
